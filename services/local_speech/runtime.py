"""CPU-only local Mandarin ASR and Chinese/English TTS runtime."""
import io,time
from pathlib import Path
import numpy as np
import sherpa_onnx
import soundfile as sf
ROOT=Path(__file__).resolve().parents[2]
MODELS=ROOT/'.cache/local-speech/models'

class SpeechRuntime:
 def __init__(self,threads=2):
  self.threads=threads
  asr=MODELS/'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
  tts=MODELS/'vits-melo-tts-zh_en'
  self.recognizer=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(asr/'model.int8.onnx'),tokens=str(asr/'tokens.txt'),num_threads=threads,provider='cpu',language='auto',use_itn=True)
  vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=str(tts/'model.onnx'),tokens=str(tts/'tokens.txt'),lexicon=str(tts/'lexicon.txt'))
  rules=','.join(str(tts/name) for name in ('date.fst','number.fst') if (tts/name).exists())
  config=sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(vits=vits,num_threads=threads,provider='cpu'),rule_fsts=rules,max_num_sentences=1)
  if not config.validate():raise ValueError('TTS model configuration is invalid')
  self.tts=sherpa_onnx.OfflineTts(config)
 def synthesize(self,text,speed=1.0):
  if not isinstance(text,str) or not text.strip() or len(text)>500:raise ValueError('text must contain 1–500 characters')
  if isinstance(speed,bool) or not isinstance(speed,(int,float)) or not 0.7<=speed<=1.5:raise ValueError('speed must be 0.7–1.5')
  start=time.perf_counter()
  audio=self.tts.generate(text.strip(),sid=0,speed=speed)
  if not len(audio.samples):raise ValueError('The model produced empty audio')
  output=io.BytesIO();sf.write(output,audio.samples,audio.sample_rate,format='WAV',subtype='PCM_16')
  return output.getvalue(),{'sample_rate':audio.sample_rate,'duration_s':len(audio.samples)/audio.sample_rate,'inference_s':round(time.perf_counter()-start,3)}
 def transcribe(self,wav):
  start=time.perf_counter()
  info=sf.info(io.BytesIO(wav))
  if info.format!='WAV' or info.channels not in (1,2) or not 8000<=info.samplerate<=48000 or not 0<info.duration<=60:raise ValueError('Use a WAV file up to 60 seconds, mono/stereo, 8–48 kHz')
  samples,rate=sf.read(io.BytesIO(wav),dtype='float32',always_2d=True)
  samples=np.ascontiguousarray(samples.mean(axis=1))
  stream=self.recognizer.create_stream();stream.accept_waveform(rate,samples);self.recognizer.decode_stream(stream)
  return {'text':stream.result.text,'audio_duration_s':info.duration,'inference_s':round(time.perf_counter()-start,3),'provider':'cpu'}
 def status(self):return {'ready':True,'provider':'cpu','threads':self.threads,'asr':'SenseVoice int8','tts':'MeloTTS zh_en','sample_rate':self.tts.sample_rate}
