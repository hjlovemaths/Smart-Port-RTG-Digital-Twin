"""Speech proxy integration through the real role middleware and CPU backend."""
import sys,io,wave,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
import pytest,httpx
from webrtc_auth import ViewerAuth,Accounts
from starlette.responses import PlainTextResponse
async def downstream(scope,receive,send):await PlainTextResponse('protected')(scope,receive,send)
@pytest.fixture
def app(tmp_path):
 accounts=Accounts(tmp_path/'accounts.json');accounts.initialize('admin','AdminTestPassword!','viewer','ViewerTestPassword!')
 return ViewerAuth(downstream,accounts)
async def login(client,username):
 await client.post('/auth/login',data={'username':username,'password':'AdminTestPassword!' if username=='admin' else 'ViewerTestPassword!'})
@pytest.mark.asyncio
async def test_voice_endpoints_require_admin(app):
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test:8011',headers={'origin':'http://test:8011'}) as c:
  assert (await c.get('/api/speech/health')).status_code==401
  await login(c,'viewer')
  for name in ('asr','tts'):assert (await c.post('/api/speech/'+name,content=b'bad',headers={'content-type':'audio/wav'})).status_code==403
@pytest.mark.asyncio
async def test_validation_and_unavailable_service(app,monkeypatch):
 import speech_web
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test:8011',headers={'origin':'http://test:8011'}) as c:
  await login(c,'admin')
  assert (await c.post('/api/speech/tts',json={'text':''})).status_code==400
  assert (await c.post('/api/speech/tts',json={'text':'x','speed':True})).status_code==400
  assert (await c.post('/api/speech/tts',json={'text':'x'*501})).status_code==400
  assert (await c.post('/api/speech/asr',content=b'bad',headers={'content-type':'text/plain'})).status_code==415
  monkeypatch.setattr(speech_web,'BASE','http://127.0.0.1:1')
  assert (await c.get('/api/speech/health')).status_code==503
@pytest.mark.asyncio
async def test_admin_http_tts_asr_roundtrip(app):
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test:8011',headers={'origin':'http://test:8011'},timeout=60) as c:
  await login(c,'admin')
  health=await c.get('/api/speech/health')
  if health.status_code==503:pytest.skip('Start local speech service for the CPU integration test')
  assert health.json()['provider']=='cpu'
  response=await c.post('/api/speech/tts',json={'text':'你好，欢迎使用智慧港口智能助手。'})
  assert response.status_code==200 and response.headers['content-type']=='audio/wav'
  with wave.open(io.BytesIO(response.content)) as wav:assert wav.getnchannels()==1 and wav.getnframes()>0
  result=await c.post('/api/speech/asr',content=response.content,headers={'content-type':'audio/wav'})
  assert result.status_code==200
  assert '智慧港口' in result.json()['text']
