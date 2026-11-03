"""Download and safely unpack official sherpa-onnx model releases locally."""
import hashlib,json,os,tarfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
MODEL_ROOT=ROOT/'.cache/local-speech/models'
MODELS={
 'asr':'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2',
 'tts':'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2',
}
def download(name,url):
 MODEL_ROOT.mkdir(parents=True,exist_ok=True)
 archive=MODEL_ROOT/url.rsplit('/',1)[-1]
 destination=MODEL_ROOT/archive.name.removesuffix('.tar.bz2')
 required=('model.int8.onnx','tokens.txt') if name=='asr' else ('model.onnx','tokens.txt','lexicon.txt')
 if not all((destination/file).is_file() for file in required):
  if not archive.exists():
   part=archive.with_suffix('.partial')
   for attempt in range(3):
    try:
     print('Downloading',name,flush=True)
     request=urllib.request.Request(url,headers={'User-Agent':'SmartPort-LocalSpeech/1.0'})
     with urllib.request.urlopen(request,timeout=60) as response,part.open('wb') as output:
      total=int(response.headers.get('Content-Length',0));size=0;last=time.monotonic()
      while chunk:=response.read(1024*1024):
       output.write(chunk);size+=len(chunk)
       if time.monotonic()-last>10:
        print(name,round(size/1024**2),'MiB /',round(total/1024**2),'MiB',flush=True);last=time.monotonic()
     if total and size!=total:raise IOError(f"Incomplete download: {size}/{total}")
     part.replace(archive);break
    except Exception:
     if attempt==2:raise
     time.sleep(2)
  print('Extracting',name,flush=True)
  with tarfile.open(archive,'r:bz2') as tf:
   root=MODEL_ROOT.resolve()
   for member in tf.getmembers():
    target=(root/member.name).resolve()
    if not target.is_relative_to(root) or member.issym() or member.islnk():raise ValueError('Unsafe archive member')
   tf.extractall(root,filter='data')
  digest=hashlib.sha256(archive.read_bytes()).hexdigest()
  (MODEL_ROOT/(name+'-receipt.json')).write_text(json.dumps({'url':url,'sha256':digest,'archive_bytes':archive.stat().st_size},indent=2),encoding='utf-8')
 print('MODEL_READY',name,destination,flush=True)
 return destination
if __name__=='__main__':
 from concurrent.futures import ThreadPoolExecutor
 with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda item:download(*item),MODELS.items()))
