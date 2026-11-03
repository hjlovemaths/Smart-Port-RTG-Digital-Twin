"""Exercise local HTTP TTS -> WAV -> ASR plus invalid-input rejection."""
import json,urllib.request,urllib.error,wave,io,time
from pathlib import Path
URL='http://127.0.0.1:8022'
def post(path,body,kind):
 request=urllib.request.Request(URL+path,data=body,headers={'Content-Type':kind},method='POST')
 return urllib.request.urlopen(request,timeout=60)
def main():
 with urllib.request.urlopen(URL+'/health') as response:health=json.load(response)
 assert health['ready'] and health['provider']=='cpu'
 text='你好，欢迎使用智慧港口智能助手。请查询设备的工作状态。'
 with post('/tts',json.dumps({'text':text},ensure_ascii=False).encode(),'application/json') as response:
  wav=response.read();tts_seconds=float(response.headers['X-Inference-Seconds'])
 with wave.open(io.BytesIO(wav)) as audio:
  assert audio.getnchannels()==1 and audio.getsampwidth()==2
  duration=audio.getnframes()/audio.getframerate()
 with post('/asr',wav,'audio/wav') as response:result=json.load(response)
 assert all(word in result['text'] for word in ('智慧港口','智能助手','工作状态')),result
 for path,body,kind,status in (('/tts',b'{"text":""}','application/json',400),('/tts',b'broken','application/json',400),('/asr',b'bad wav','audio/wav',400),('/asr',b'bad','text/plain',415)):
  try:post(path,body,kind);raise AssertionError('Invalid input was accepted')
  except urllib.error.HTTPError as error:assert error.code==status,(path,error.code,error.read())
 root=Path(__file__).resolve().parents[2]/'outputs';root.mkdir(exist_ok=True)
 (root/'local-speech-demo.wav').write_bytes(wav)
 report={'health':health,'text':text,'audio_duration_s':round(duration,3),'tts_inference_s':tts_seconds,'asr':result,'invalid_input_checks':'passed'}
 (root/'local-speech-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=True,indent=2))
if __name__=='__main__':main()
