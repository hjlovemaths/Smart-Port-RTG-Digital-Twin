from pathlib import Path
import sys
sys.path.insert(0,str(Path('omniverse/scripts').resolve()))
from webrtc_auth import Accounts,ViewerAuth,COOKIE,activate_pending_sts_account
from starlette.responses import PlainTextResponse
import httpx,pytest

async def app(scope,receive,send):await PlainTextResponse('protected')(scope,receive,send)
@pytest.fixture
def server(tmp_path):return ViewerAuth(app,Accounts(tmp_path/'accounts.json'))
def initialize(server):server.accounts.initialize('admin','AdminTestPassword!','viewer','ViewerTestPassword!')
@pytest.mark.asyncio
async def test_setup_only_on_loopback_and_stores_hashes(server):
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server,client=('192.168.6.90',123)),base_url='http://host:8011') as c:
  assert (await c.post('/auth/setup',headers={'origin':'http://host:8011'},data={})).status_code==403
 initialize(server)
 assert 'AdminTestPassword' not in server.accounts.path.read_text()
 with pytest.raises(ValueError):initialize(server)
@pytest.mark.asyncio
async def test_login_roles_and_logout(server):
 initialize(server)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://test:8011',headers={'origin':'http://test:8011'}) as c:
  assert (await c.get('/')).status_code==303
  assert (await c.get('/api/smartport-agent/state')).status_code==401
  assert (await c.post('/auth/login',data={'username':'viewer','password':'wrong'})).status_code==400
  r=await c.post('/auth/login',data={'username':'viewer','password':'ViewerTestPassword!'})
  assert r.status_code==303 and r.headers['location']=='/?spectator=1'
  assert 'HttpOnly' in r.headers['set-cookie']
  assert (await c.get('/')).headers['location']=='/?spectator=1'
  assert (await c.get('/?spectator=1')).status_code==200
  for path in ('navigate','send','settings','view'):
   assert (await c.post('/api/smartport-agent/'+path,json={})).status_code==403
  assert (await c.get('/api/transfer-camera/state')).status_code==200
  assert (await c.post('/api/transfer-camera/state',json={})).status_code==403
  assert (await c.post('/api/smartport-agent/cinema',json={'action':'resume'})).status_code==403
  assert len((await c.get('/api/stream-config')).json()['streams'])==1
  token=c.cookies.get(COOKIE)
  assert (await c.post('/auth/logout')).status_code==303
  assert server.accounts.session(token) is None
  await c.post('/auth/login',data={'username':'admin','password':'AdminTestPassword!'})
  assert (await c.post('/api/smartport-agent/navigate',json={})).status_code==200
@pytest.mark.asyncio
async def test_csrf_expiry_and_throttle(server):
 initialize(server)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://test:8011') as c:
  assert (await c.post('/auth/login',data={})).status_code==403
 token=server.accounts.login('viewer','ViewerTestPassword!','x')
 server.accounts.sessions[token]['expires']=0
 assert server.accounts.session(token) is None
 for _ in range(8):
  with pytest.raises(ValueError):server.accounts.login('viewer','wrong','x')
 with pytest.raises(ValueError,match='尝试次数'):server.accounts.login('viewer','ViewerTestPassword!','x')

def test_three_viewer_sessions_have_distinct_streams_and_expiry(server):
 initialize(server)
 sessions=[server.accounts.session(server.accounts.login('viewer','ViewerTestPassword!',str(i))) for i in range(4)]
 assert [server.accounts.spectator_port(s) for s in sessions[:3]]==[49200,49210,49220]
 with pytest.raises(ValueError,match='均已占用'):server.accounts.spectator_port(sessions[3])
 sessions[0]['lease_until']=0
 assert server.accounts.spectator_port(sessions[3])==49200


@pytest.mark.asyncio
async def test_admin_creates_driver_account_login_routes_and_driver_cannot_administer(server):
 initialize(server)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://test:8011',headers={'origin':'http://test:8011'}) as c:
  await c.post('/auth/login',data={'username':'viewer','password':'ViewerTestPassword!'})
  assert (await c.post('/auth/driver-account',data={'username':'driver','password':'DriverTestPassword!'})).status_code==403
  await c.post('/auth/login',data={'username':'admin','password':'AdminTestPassword!'})
  assert (await c.get('/auth/driver-account')).status_code==200
  assert (await c.get('/driver')).headers['location']=='/'
  assert (await c.post('/auth/driver-account',data={'username':'admin','password':'DriverTestPassword!'})).status_code==400
  assert (await c.post('/auth/driver-account',data={'username':'driver','password':'DriverTestPassword!'})).status_code==200
  assert 'DriverTestPassword!' not in server.accounts.path.read_text()
  response=await c.post('/auth/login',data={'username':'driver','password':'DriverTestPassword!'})
  assert response.headers['location']=='/driver'
  assert (await c.get('/')).headers['location']=='/driver'
  assert (await c.get('/driver')).status_code==200
  assert (await c.get('/auth/me')).json()['role']=='driver'
  stream=(await c.get('/api/stream-config')).json()['streams']
  assert len(stream)==1 and stream[0]['type']=='spectator' and stream[0]['port']==49230
  assert all(s['port']!=49100 for s in stream)
  assert (await c.post('/api/truck-driver/input',json={'throttle':1})).status_code==200
  for path in ('/api/smartport-agent/state','/api/smartport-agent/view','/api/speech/transcribe','/auth/driver-account'):
   assert (await c.get(path)).status_code==403
  assert (await c.get('/static/smartport-driver.js')).status_code==200
  assert (await c.post('/static/smartport-driver.js')).status_code==403
  token=c.cookies.get(COOKIE);session=server.accounts.session(token)
  await c.post('/auth/logout');assert session['driver_released']


def test_driver_password_rotation_revokes_old_session_and_preserves_other_roles(server):
 initialize(server);before=server.accounts.read()
 server.accounts.set_driver('driver','DriverTestPassword!')
 token=server.accounts.login('driver','DriverTestPassword!','driver-ip')
 session=server.accounts.session(token)
 with pytest.raises(ValueError):server.accounts.set_driver('otherdriver','OtherDriverPassword!')
 server.accounts.set_driver('driver','ReplacementPassword!')
 assert server.accounts.session(token) is None and session['driver_released']
 assert all(server.accounts.read()[name]==record for name,record in before.items())


def test_stream_allocation_skips_unavailable_servers(tmp_path):
 accounts=Accounts(tmp_path/'accounts.json',available_ports=lambda:(49210,49220))
 accounts.initialize('admin','AdminTestPassword!','viewer','ViewerTestPassword!')
 session=accounts.session(accounts.login('viewer','ViewerTestPassword!','x'))
 assert accounts.spectator_port(session)==49210


@pytest.mark.asyncio
async def test_simultaneous_roles_keep_distinct_entrypoints_and_streams(server):
 initialize(server);server.accounts.set_driver('driver','DriverTestPassword!')
 transport=httpx.ASGITransport(app=server)
 clients=[httpx.AsyncClient(transport=transport,base_url='http://test:8011',headers={'origin':'http://test:8011'}) for _ in range(3)]
 try:
  admin,driver,viewer=clients
  for client,name,password in ((admin,'admin','AdminTestPassword!'),(driver,'driver','DriverTestPassword!'),(viewer,'viewer','ViewerTestPassword!')):
   assert (await client.post('/auth/login',data={'username':name,'password':password})).status_code==303
  assert (await admin.get('/auth/me')).json()['initial_view_pending']
  assert not (await driver.get('/auth/me')).json()['initial_view_pending']
  assert (await admin.get('/driver')).headers['location']=='/'
  assert (await driver.get('/')).headers['location']=='/driver'
  assert (await viewer.get('/driver')).headers['location']=='/?spectator=1'
  assert (await driver.get('/api/stream-config')).json()['streams'][0]['port']==49230
  assert (await viewer.get('/api/stream-config')).json()['streams'][0]['port'] in (49200,49210,49220)
  assert (await admin.get('/api/stream-config')).text=='protected'
  for client in (viewer,driver):
   assert (await client.post('/api/session/initial-view')).status_code==403
  assert (await admin.post('/api/truck-driver/acquire',json={})).status_code==403
 finally:
  for client in clients:await client.aclose()


@pytest.mark.asyncio
async def test_rtg_account_controls_only_rtg_and_does_not_revoke_truck(server):
 initialize(server);server.accounts.set_driver('ITK01','DriverTestPassword!')
 transport=httpx.ASGITransport(app=server)
 async with httpx.AsyncClient(transport=transport,base_url='http://test:8011',headers={'origin':'http://test:8011'}) as admin, httpx.AsyncClient(transport=transport,base_url='http://test:8011',headers={'origin':'http://test:8011'}) as truck, httpx.AsyncClient(transport=transport,base_url='http://test:8011',headers={'origin':'http://test:8011'}) as rtg:
  await admin.post('/auth/login',data={'username':'admin','password':'AdminTestPassword!'})
  await truck.post('/auth/login',data={'username':'ITK01','password':'DriverTestPassword!'})
  truck_session=server.accounts.session(truck.cookies.get(COOKIE))
  response=await admin.post('/auth/rtg-driver-account',data={'username':'RTG01','password':'RTGTestPassword!'})
  assert response.status_code==200 and 'RTG司机' in response.text
  assert (await rtg.post('/auth/login',data={'username':'RTG01','password':'RTGTestPassword!'})).headers['location']=='/rtg-driver'
  assert (await rtg.get('/')).headers['location']=='/rtg-driver'
  assert (await rtg.get('/driver')).headers['location']=='/rtg-driver'
  assert (await rtg.get('/rtg-driver')).status_code==200
  assert (await rtg.get('/api/stream-config')).json()['streams'][0]['port']==49240
  assert (await truck.get('/api/stream-config')).json()['streams'][0]['port']==49230
  assert (await admin.get('/rtg-driver')).headers['location']=='/'
  for actor in (admin,truck):assert (await actor.post('/api/rtg-driver/input',json={})).status_code==403
  for path in ('/api/truck-driver/input','/api/smartport-agent/navigate','/api/session/initial-view','/auth/driver-account'):
   assert (await rtg.post(path,json={})).status_code==403
  assert (await rtg.post('/api/rtg-driver/input',json={'gantry':1})).status_code==200
  assert (await rtg.get('/api/rtg-driver/input')).status_code==405
  await rtg.post('/auth/logout')
  assert server.accounts.session(truck.cookies.get(COOKIE)) is truck_session
  assert not truck_session.get('driver_released')
  assert (await truck.post('/api/truck-driver/input',json={'throttle':1})).status_code==200


def test_rtg_password_update_preserves_truck_and_admin_accounts(server):
 initialize(server);server.accounts.set_driver('ITK01','DriverTestPassword!')
 before=server.accounts.read();server.accounts.set_driver('RTG01','RTGTestPassword!','rtg_driver')
 token=server.accounts.login('RTG01','RTGTestPassword!','rtg-ip')
 with pytest.raises(ValueError):server.accounts.set_driver('ITK01','RTGTestPassword!','rtg_driver')
 with pytest.raises(ValueError):server.accounts.set_driver('RTG02','RTGTestPassword!','rtg_driver')
 server.accounts.set_driver('RTG01','RTGReplacementPassword!','rtg_driver')
 assert server.accounts.session(token) is None
 assert all(server.accounts.read()[name]==record for name,record in before.items())


@pytest.mark.asyncio
async def test_sts_account_is_separate_from_truck_rtg_and_admin(server):
 initialize(server);server.accounts.set_driver('ITK01','DriverTestPassword!');server.accounts.set_driver('RTG01','RTGTestPassword!','rtg_driver')
 transport=httpx.ASGITransport(app=server)
 async with httpx.AsyncClient(transport=transport,base_url='http://test:8011',headers={'origin':'http://test:8011'}) as admin, httpx.AsyncClient(transport=transport,base_url='http://test:8011',headers={'origin':'http://test:8011'}) as sts, httpx.AsyncClient(transport=transport,base_url='http://test:8011',headers={'origin':'http://test:8011'}) as rtg:
  await admin.post('/auth/login',data={'username':'admin','password':'AdminTestPassword!'})
  await rtg.post('/auth/login',data={'username':'RTG01','password':'RTGTestPassword!'})
  assert (await admin.post('/auth/sts-driver-account',data={'username':'STS01','password':'STSTestPassword!'})).status_code==200
  assert (await sts.post('/auth/login',data={'username':'STS01','password':'STSTestPassword!'})).headers['location']=='/sts-driver'
  assert (await sts.get('/auth/me')).json()['role']=='sts_driver'
  assert (await sts.get('/api/stream-config')).json()['streams'][0]['port']==49250
  assert (await rtg.get('/api/stream-config')).json()['streams'][0]['port']==49240
  assert (await sts.post('/api/sts-driver/input',json={})).status_code==200
  assert (await sts.post('/api/rtg-driver/input',json={})).status_code==403
  assert (await sts.post('/api/truck-driver/input',json={})).status_code==403
  assert (await rtg.post('/api/sts-driver/input',json={})).status_code==403
  assert (await admin.post('/api/sts-driver/input',json={})).status_code==403
  assert (await sts.post('/api/smartport-agent/navigate',json={})).status_code==403


def test_pending_sts_role_activates_only_under_new_auth_without_overwriting(server):
 initialize(server);before=server.accounts.read()
 other=Accounts(server.accounts.path.with_name('temporary.json'))
 other.initialize('admin','AdminTestPassword!','viewer','ViewerTestPassword!')
 other.set_driver('STS01','STSTestPassword!','sts_driver')
 import json
 pending=server.accounts.path.with_name('pending-sts-account.json')
 pending.write_text(json.dumps({'username':'STS01','record':other.read()['STS01']}),encoding='utf-8')
 assert 'STS01' not in server.accounts.read()
 activate_pending_sts_account(server.accounts)
 assert not pending.exists()
 assert all(server.accounts.read()[name]==value for name,value in before.items())
 assert server.accounts.session(server.accounts.login('STS01','STSTestPassword!','x'))['role']=='sts_driver'
