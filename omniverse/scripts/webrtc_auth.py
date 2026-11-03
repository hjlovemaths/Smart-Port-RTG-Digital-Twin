"""Account login and role enforcement for the HTTP WebRTC viewer service."""
import hashlib,hmac,html,json,secrets,time,threading
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from starlette.requests import Request
from starlette.responses import HTMLResponse,JSONResponse,RedirectResponse

STORE=Path(__file__).resolve().parents[2]/'.cache/webrtc-auth/accounts.json'
COOKIE='smartport_session'
TTL=8*3600

def password_hash(password,salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()

class Accounts:
    def __init__(self,path=STORE):
        self.path=Path(path);self.sessions={};self.attempts={};self.lock=threading.Lock()
    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else None
    def initialize(self,admin,admin_password,viewer,viewer_password):
        with self.lock:
            if self.path.exists():raise ValueError('账号已初始化')
            if admin==viewer:raise ValueError('管理员与观众账号不能相同')
            records={}
            for name,password,role in ((admin,admin_password,'admin'),(viewer,viewer_password,'viewer')):
                if not 1<=len(name)<=64 or not name.isascii() or not all(c.isalnum() or c in '_-.' for c in name):raise ValueError('账号限1–64位英文字母、数字、下划线、点或短横线')
                if not 10<=len(password)<=128:raise ValueError('密码长度须为10–128位')
                salt=secrets.token_hex(16)
                records[name]={'role':role,'salt':salt,'hash':password_hash(password,salt)}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('x',encoding='utf-8') as f:json.dump(records,f)
    def login(self,username,password,ip):
        now=time.monotonic()
        attempts=[t for t in self.attempts.get(ip,[]) if now-t<60]
        if len(attempts)>=8:raise ValueError('尝试次数过多，请一分钟后重试')
        attempts.append(now);self.attempts[ip]=attempts
        record=(self.read() or {}).get(username)
        salt=record['salt'] if record else '00'*16
        digest=password_hash(password,salt)
        if not record or not hmac.compare_digest(digest,record['hash']):raise ValueError('账号或密码错误')
        self.attempts.pop(ip,None)
        self.sessions={k:v for k,v in self.sessions.items() if v['expires']>time.time()}
        token=secrets.token_urlsafe(32)
        self.sessions[token]={'username':username,'role':record['role'],'expires':time.time()+TTL}
        return token
    def spectator_port(self,session):
        now=time.time()
        active={v.get('spectator_port') for v in self.sessions.values() if v is not session and v.get('lease_until',0)>now and v['expires']>now}
        port=session.get('spectator_port')
        if port not in (49200,49210,49220) or port in active:
            port=next((p for p in (49200,49210,49220) if p not in active),None)
        if port is None:raise ValueError('3路观众流均已占用，请稍后再试')
        session.update(spectator_port=port,lease_until=now+90)
        return port
    def session(self,token):
        value=self.sessions.get(token)
        return value if value and value['expires']>time.time() else None

def page(setup=False,error=''):
    fields='''<label>管理员账号<input name="admin" value="admin" autocomplete="username" required></label><label>管理员密码<input name="admin_password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required></label><label>观众账号<input name="viewer" value="viewer" required></label><label>观众密码<input name="viewer_password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required></label>''' if setup else '''<label>账号<input name="username" autocomplete="username" required autofocus></label><label>密码<input name="password" type="password" autocomplete="current-password" required></label>'''
    title='初始化港区账号' if setup else '登录智慧港口'
    description='请设置管理员和观众账号，密码至少10位。' if setup else '管理员可操作场景，观众可同步观看。'
    body='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>智慧港口 · 登录</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#09131e;color:#e6eef6;font:16px system-ui}main{width:min(360px,80vw);padding:36px;background:#152536;border:1px solid #334c64;border-radius:16px;box-shadow:0 24px 80px #0005}h1{font-size:25px;margin:8px 0}p{color:#a9bccd;line-height:1.6}label{display:grid;gap:7px;margin:17px 0}input{box-sizing:border-box;width:100%;padding:11px;background:#0b1927;border:1px solid #49647c;border-radius:6px;color:white;font:inherit}button{width:100%;padding:12px;border:0;border-radius:6px;background:#2d8bd8;color:white;font:inherit;cursor:pointer}.error{color:#ffb2a9}</style><main><small>SMART PORT · DIGITAL TWIN</small>'''
    return HTMLResponse(body+f'<h1>{title}</h1><p>{description}</p><p class="error">{html.escape(error)}</p><form method="post" action="/auth/{"setup" if setup else "login"}">{fields}<button>{"保存账号并登录" if setup else "登录"}</button></form></main></html>',status_code=400 if error else 200)

class ViewerAuth:
    def __init__(self,app,accounts=None):self.app=app;self.accounts=accounts or Accounts()
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        request=Request(scope,receive);path=request.url.path
        try:response=await self.handle(request,path)
        except ValueError as error:response=page(path=='/auth/setup',str(error))
        if response is None:return await self.app(scope,receive,send)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Frame-Options']='DENY'
        await response(scope,receive,send)
    async def handle(self,r,path):
        host=r.url.hostname
        local=r.client and r.client.host in ('127.0.0.1','::1') and host in ('localhost','127.0.0.1','::1')
        configured=self.accounts.path.exists()
        if r.method not in ('GET','HEAD','OPTIONS'):
            origin=r.headers.get('origin')
            if not origin or urlsplit(origin).netloc!=r.headers.get('host'):return JSONResponse({'detail':'请求来源不匹配'},403)
        if path=='/auth/setup':
            if not local:return HTMLResponse('请先在服务器本机打开 http://127.0.0.1:8011 完成账号初始化。',403)
            if configured:return RedirectResponse('/login',303)
            if r.method=='GET':return page(True)
            if r.method!='POST':return JSONResponse({},405)
            data=await self.form(r)
            self.accounts.initialize(data.get('admin','').strip(),data.get('admin_password',''),data.get('viewer','').strip(),data.get('viewer_password',''))
            return RedirectResponse('/login',303)
        if not configured:
            return RedirectResponse('/auth/setup',303) if local else HTMLResponse('管理员尚未初始化账号，请稍后重试。',503)
        session=self.accounts.session(r.cookies.get(COOKIE))
        if path=='/login':return page()
        if path=='/auth/login':
            if r.method!='POST':return JSONResponse({},405)
            data=await self.form(r)
            token=self.accounts.login(data.get('username','').strip(),data.get('password',''),r.client.host if r.client else '')
            role=self.accounts.session(token)['role']
            response=RedirectResponse('/?spectator=1' if role=='viewer' else '/',303)
            response.set_cookie(COOKIE,token,max_age=TTL,httponly=True,samesite='strict',secure=r.url.scheme=='https')
            return response
        if not session:
            return JSONResponse({'detail':'请先登录'},401) if path.startswith('/api/') else RedirectResponse('/login',303)
        if path=='/auth/logout':
            if r.method!='POST':return JSONResponse({},405)
            self.accounts.sessions.pop(r.cookies.get(COOKIE),None)
            response=RedirectResponse('/login',303);response.delete_cookie(COOKIE);return response
        if path=='/auth/release':
            if r.method!='POST':return JSONResponse({},405)
            session.pop('spectator_port',None);session.pop('lease_until',None)
            return JSONResponse({'ok':True})
        if path=='/auth/me':
            if session.get('spectator_port'):session['lease_until']=time.time()+90
            return JSONResponse({'username':session['username'],'role':session['role']})
        if path=='/api/stream-config' and (session['role']=='viewer' or r.query_params.get('spectator')=='1'):
            try:port=self.accounts.spectator_port(session)
            except ValueError as error:return JSONResponse({'detail':str(error)},409)
            return JSONResponse({'streams':[{'name':'观众流 '+str((port-49200)//10+1),'type':'spectator','port':port,'streamType':'webrtc','allowDynamicResize':False,'spectatorIndex':(port-49200)//10}]})
        if path.startswith('/api/speech/'):
            if session['role']!='admin':return JSONResponse({'detail':'仅管理员可使用语音功能'},403)
            from speech_web import handle_speech
            return await handle_speech(r)
        if session['role']=='viewer':
            if path=='/api/transfer-camera/state' and r.method=='GET':return None
            if path in ('/','/index.html'):
                if r.query_params.get('spectator')!='1':return RedirectResponse('/?spectator=1',303)
                return None
            if path=='/api/stream-config':
                return JSONResponse({'streams':[{'name':'观众流','type':'spectator','port':49200,'streamType':'webrtc','allowDynamicResize':False,'spectatorIndex':0}]})
            if not path.startswith('/static/'):return JSONResponse({'detail':'观众账号没有操作权限'},403)
            if r.method not in ('GET','HEAD'):return JSONResponse({},403)
        return None
    async def form(self,r):
        body=bytearray()
        async for part in r.stream():
            body.extend(part)
            if len(body)>4096:raise ValueError('提交内容过长')
        return {k:v[-1] for k,v in parse_qs(body.decode('utf-8')).items()}

def install_auth(app):
    if getattr(app.state,'smartport_auth_installed',False):return
    # Kit has already started its ASGI service by the time the Agent installs.
    app.middleware_stack=ViewerAuth(app.middleware_stack or app.build_middleware_stack())
    app.state.smartport_auth_installed=True
