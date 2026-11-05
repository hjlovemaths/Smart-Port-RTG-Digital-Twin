"""Exercise the actual endpoint factory with authenticated ASGI requests."""
import ast,asyncio,json,re,sys
from pathlib import Path
import httpx,pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
from webrtc_auth import Accounts,ViewerAuth,COOKIE


@pytest.mark.asyncio
async def test_driver_endpoint_uses_verified_identity_and_validates_payload(tmp_path):
    source=Path('omniverse/extensions/smartport.agent_panel/smartport/agent_panel/web_bridge.py')
    tree=ast.parse(source.read_text(encoding='utf-8'))
    factory=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='driver_endpoint')
    calls=[]
    async def execute(action,payload):calls.append((action,payload));return {'vehicle_id':'01'}
    namespace=dict(execute=execute,Request=Request,json=json,re=re,HTTPException=HTTPException)
    exec(compile(ast.Module(body=[factory],type_ignores=[]),str(source),'exec'),namespace)
    endpoint=namespace['driver_endpoint']('input')
    async def app(scope,receive,send):
        try:response=JSONResponse(await endpoint(Request(scope,receive)))
        except HTTPException as error:response=JSONResponse({'detail':error.detail},error.status_code)
        await response(scope,receive,send)
    accounts=Accounts(tmp_path/'accounts.json');accounts.initialize('admin','AdminTestPassword!','viewer','ViewerTestPassword!')
    accounts.set_driver('driver','DriverTestPassword!');server=ViewerAuth(app,accounts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://test:8011',headers={'origin':'http://test:8011'}) as client:
        await client.post('/auth/login',data={'username':'driver','password':'DriverTestPassword!'})
        assert (await client.post('/api/truck-driver/input',json={'throttle':1})).status_code==400
        assert (await client.post('/api/truck-driver/input',json=[])).status_code==400
        assert (await client.post('/api/truck-driver/input',content='x'*4100)).status_code==400
        response=await client.post('/api/truck-driver/input',json={'client_id':'page-123456789012','throttle':1})
        assert response.status_code==200
        action,payload=calls[-1]
        assert action=='driver_input'
        assert payload['owner']==client.cookies.get(COOKIE)+':page-123456789012'
        assert payload['session']['role']=='driver' and payload['data']=={'throttle':1}
        await client.post('/api/truck-driver/input',json={'client_id':'second-page-12345','throttle':1})
        assert calls[-1][1]['owner']!=payload['owner'],'separate tabs must have separate vehicle owners'
        await client.post('/auth/login',data={'username':'viewer','password':'ViewerTestPassword!'})
        assert (await client.post('/api/truck-driver/input',json={'client_id':'page-123456789012'})).status_code==403


@pytest.mark.asyncio
async def test_rtg_endpoint_uses_distinct_role_action_and_owner(tmp_path):
    source=Path('omniverse/extensions/smartport.agent_panel/smartport/agent_panel/web_bridge.py')
    factory=next(n for n in ast.walk(ast.parse(source.read_text(encoding='utf-8'))) if isinstance(n,ast.FunctionDef) and n.name=='driver_endpoint')
    calls=[]
    async def execute(action,payload):calls.append((action,payload));return {'vehicle_id':'RTG_PRIMARY'}
    namespace=dict(execute=execute,Request=Request,json=json,re=re,HTTPException=HTTPException)
    exec(compile(ast.Module(body=[factory],type_ignores=[]),str(source),'exec'),namespace)
    endpoint=namespace['driver_endpoint']('input','rtg_driver','rtg_driver_')
    async def app(scope,receive,send):
        try:response=JSONResponse(await endpoint(Request(scope,receive)))
        except HTTPException as error:response=JSONResponse({'detail':error.detail},error.status_code)
        await response(scope,receive,send)
    accounts=Accounts(tmp_path/'accounts.json');accounts.initialize('admin','AdminTestPassword!','viewer','ViewerTestPassword!')
    accounts.set_driver('RTG01','RTGTestPassword!','rtg_driver')
    server=ViewerAuth(app,accounts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://test:8011',headers={'origin':'http://test:8011'}) as client:
        await client.post('/auth/login',data={'username':'RTG01','password':'RTGTestPassword!'})
        assert (await client.post('/api/rtg-driver/input',json={'client_id':'rtg-page-12345678','gantry':1,'owner':'forged'})).status_code==200
        action,payload=calls[-1]
        assert action=='rtg_driver_input'
        assert payload['owner']==client.cookies.get(COOKIE)+':rtg-page-12345678'
        assert payload['session']['role']=='rtg_driver'
        # An untrusted owner field remains only in data, never in the verified identity.
        assert payload['data']['owner']=='forged'
        assert (await client.post('/api/rtg-driver/input',json={'client_id':'bad'})).status_code==400
        await client.post('/auth/login',data={'username':'admin','password':'AdminTestPassword!'})
        assert (await client.post('/api/rtg-driver/input',json={'client_id':'rtg-page-12345678'})).status_code==403
