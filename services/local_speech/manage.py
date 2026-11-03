"""Start/stop the isolated, loopback-only speech service without opening a window."""
import argparse,json,os,socket,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CACHE=ROOT/'.cache/local-speech'
PYTHON=CACHE/'venv/Scripts/python.exe'
URL='http://127.0.0.1:8022'

def health():
 with urllib.request.urlopen(URL+'/health',timeout=2) as r:return json.load(r)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','status'],default='start',nargs='?');args=parser.parse_args()
 try:state=health()
 except Exception:state=None
 if args.action=='status':print(json.dumps(state or {'ready':False},ensure_ascii=False));return 0 if state else 1
 if args.action=='stop':
  if state:
   saved=json.loads((CACHE/'service.json').read_text(encoding='utf-8'))
   if saved['pid']!=state.get('pid') or state.get('asr')!='SenseVoice int8':raise RuntimeError('Unexpected process; refusing to stop')
   import signal
   os.kill(state['pid'],signal.SIGTERM)
  print('Speech service stopped');return 0
 if state:
  if state.get('asr')!='SenseVoice int8':raise RuntimeError('Port 8022 belongs to another service')
  (CACHE/'service.json').write_text(json.dumps({'pid':state['pid'],'url':URL},indent=2),encoding='utf-8')
  print(json.dumps(state,ensure_ascii=False));return 0
 if not PYTHON.is_file():raise RuntimeError('Run setup_local_speech.cmd first')
 with socket.socket() as probe:
  if probe.connect_ex(('127.0.0.1',8022))==0:raise RuntimeError('Port 8022 is occupied')
 CACHE.mkdir(parents=True,exist_ok=True)
 with (CACHE/'service.log').open('ab') as log:
  proc=subprocess.Popen([str(PYTHON),str(ROOT/'services/local_speech/server.py')],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env=dict(os.environ,PYTHONUTF8='1',OMP_NUM_THREADS='2'))
 (CACHE/'service.json').write_text(json.dumps({'pid':proc.pid,'url':URL},indent=2),encoding='utf-8')
 for _ in range(60):
  if proc.poll() is not None:raise RuntimeError('Speech service exited; see .cache/local-speech/service.log')
  try:
   state=health()
   if state.get('ready') and state.get('asr')=='SenseVoice int8':
    (CACHE/'service.json').write_text(json.dumps({'pid':state['pid'],'url':URL},indent=2),encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False));return 0
  except Exception:pass
  time.sleep(.5)
 raise RuntimeError('Speech service startup timed out; see log')
if __name__=='__main__':raise SystemExit(main())
