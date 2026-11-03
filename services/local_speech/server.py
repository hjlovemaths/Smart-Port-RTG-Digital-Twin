"""Loopback HTTP service for local speech models. No cloud calls or audio retention."""
import argparse,json,threading,os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from runtime import SpeechRuntime

class Server(ThreadingHTTPServer):
 daemon_threads=True
 allow_reuse_address=True
class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):pass
 def send_body(self,status,data,content_type='application/json; charset=utf-8',headers=None):
  if isinstance(data,dict):data=json.dumps(data,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
  for k,v in (headers or {}).items():self.send_header(k,str(v))
  self.end_headers();self.wfile.write(data)
 def do_GET(self):
  if self.path=='/health':self.send_body(200,{'pid':os.getpid(),**self.server.runtime.status()})
  else:self.send_body(404,{'error':'Not found'})
 def do_POST(self):
  if self.path not in ('/asr','/tts'):return self.send_body(404,{'error':'Not found'})
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=12*1024*1024:return self.send_body(413,{'error':'Body must be 1 byte to 12 MiB'})
   expected='application/json' if self.path=='/tts' else 'audio/wav'
   if self.headers.get_content_type()!=expected:return self.send_body(415,{'error':'Expected '+expected})
   self.connection.settimeout(15);data=self.rfile.read(size)
   if len(data)!=size:raise ValueError('Incomplete request body')
   if not self.server.gate.acquire(blocking=False):return self.send_body(503,{'error':'Speech service busy; retry shortly'})
   try:
    if self.path=='/tts':
     args=json.loads(data)
     if not isinstance(args,dict) or set(args)-{'text','speed'}:raise ValueError('Expected text and optional speed')
     wav,info=self.server.runtime.synthesize(args.get('text'),args.get('speed',1))
     self.send_body(200,wav,'audio/wav',{'X-Inference-Seconds':info['inference_s'],'X-Audio-Duration':round(info['duration_s'],3)})
    else:self.send_body(200,self.server.runtime.transcribe(data))
   finally:self.server.gate.release()
  except (ValueError,RuntimeError,TypeError) as error:self.send_body(400,{'error':str(error)[:200]})
  except Exception:self.send_body(500,{'error':'Local speech processing failed'})

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8022);parser.add_argument('--threads',type=int,default=2);args=parser.parse_args()
 if not 1<=args.threads<=4:parser.error('Use 1–4 CPU threads')
 runtime=SpeechRuntime(args.threads)
 server=Server(('127.0.0.1',args.port),Handler);server.runtime=runtime;server.gate=threading.BoundedSemaphore(1)
 print(json.dumps({'url':f'http://127.0.0.1:{args.port}',**runtime.status()},ensure_ascii=False),flush=True)
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:server.server_close()
if __name__=='__main__':main()
