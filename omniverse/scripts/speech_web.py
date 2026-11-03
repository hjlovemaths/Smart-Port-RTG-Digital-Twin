"""Authenticated same-origin proxy to the loopback-only CPU speech service."""
import asyncio,json,urllib.request,urllib.error
from starlette.responses import JSONResponse,Response
BASE='http://127.0.0.1:8022'
MAX_AUDIO=12*1024*1024

def call_local(path,body=None,content_type=None):
    headers={'Content-Type':content_type} if content_type else {}
    request=urllib.request.Request(BASE+path,data=body,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=45) as upstream:
            data=upstream.read(12*1024*1024+1)
            if len(data)>12*1024*1024:return JSONResponse({'detail':'语音响应过大'},502)
            return Response(data,status_code=200,media_type=upstream.headers.get_content_type(),headers={'Cache-Control':'no-store'})
    except urllib.error.HTTPError as error:
        try:message=json.loads(error.read(4096)).get('error','语音处理失败')
        except Exception:message='语音处理失败'
        return JSONResponse({'detail':message},status_code=error.code if error.code in (400,413,415,503) else 502)
    except (urllib.error.URLError,TimeoutError,OSError):
        return JSONResponse({'detail':'本地语音服务未就绪，请启动 start_local_speech.cmd 后重试'},503)

async def handle_speech(request):
    path=request.url.path.removeprefix('/api/speech')
    if path=='/health' and request.method=='GET':return await asyncio.to_thread(call_local,path)
    if path not in ('/asr','/tts'):return JSONResponse({'detail':'语音接口不存在'},404)
    if request.method!='POST':return JSONResponse({'detail':'请使用POST'},405)
    kind='audio/wav' if path=='/asr' else 'application/json'
    if request.headers.get('content-type','').split(';')[0].strip().lower()!=kind:return JSONResponse({'detail':'不支持的音频或请求格式'},415)
    limit=MAX_AUDIO if path=='/asr' else 8192
    body=bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body)>limit:return JSONResponse({'detail':'音频最长60秒或文本过长'},413)
    if not body:return JSONResponse({'detail':'内容为空'},400)
    if path=='/tts':
        try:
            payload=json.loads(body)
            if not isinstance(payload,dict) or set(payload)-{'text','speed'}:raise ValueError()
            text=payload.get('text');speed=payload.get('speed',1)
            if not isinstance(text,str) or not text.strip() or len(text)>500 or type(speed) not in (int,float) or not .7<=speed<=1.5:raise ValueError()
        except (ValueError,TypeError,UnicodeError):return JSONResponse({'detail':'朗读文字需为1–500字，语速0.7–1.5'},400)
    return await asyncio.to_thread(call_local,path,bytes(body),kind)
