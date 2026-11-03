from pathlib import Path
import sys
sys.path.insert(0,str(Path('omniverse/scripts').resolve()))
from webrtc_auth import Accounts,ViewerAuth,COOKIE
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
