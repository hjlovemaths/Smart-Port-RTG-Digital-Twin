// PCM/WAV encoding is independent of microphone hardware and cloud services.
export function encodeWav(chunks,sampleRate){
  const count=chunks.reduce((n,c)=>n+c.length,0),buffer=new ArrayBuffer(44+count*2),view=new DataView(buffer);
  const tag=(offset,s)=>{for(let i=0;i<s.length;i++)view.setUint8(offset+i,s.charCodeAt(i));};
  tag(0,'RIFF');view.setUint32(4,36+count*2,true);tag(8,'WAVE');tag(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);
  tag(36,'data');view.setUint32(40,count*2,true);let offset=44;
  for(const chunk of chunks)for(const value of chunk){const x=Math.max(-1,Math.min(1,value));view.setInt16(offset,x<0?x*32768:x*32767,true);offset+=2;}
  return new Blob([buffer],{type:'audio/wav'});
}
export function spokenChunks(text){
  text=text.replace(/```[\s\S]*?```/g,'').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/[*#`_]/g,'').trim();
  if(text.length>4000)throw new Error('回答较长，请选择文字或精简回答后再朗读');
  const parts=[];let current='';
  for(const sentence of text.match(/[^。！？!?；;\n]+[。！？!?；;\n]*/g)||[]){
    for(let i=0;i<sentence.length;i+=220){const part=sentence.slice(i,i+220);if(current.length+part.length>220){parts.push(current);current='';}current+=part;}
  }
  if(current.trim())parts.push(current);return parts.filter(p=>p.trim());
}

export function attachSpeech({host,input,responseCard,onDraft}){
  const bar=document.createElement('div');bar.className='web-speech-controls';
  bar.innerHTML='<button type="button" data-action="mic" aria-pressed="false">语音输入</button><button type="button" data-action="cancel" hidden>取消录音</button><button type="button" data-action="file">识别音频</button><label><input type="checkbox" data-action="auto">自动朗读</label><button type="button" data-action="stop" disabled>停止朗读</button><input type="file" accept=".wav,audio/wav" data-action="upload" hidden><span class="web-speech-status" role="status" aria-live="polite"></span>';
  host.append(bar);
  const find=name=>bar.querySelector(`[data-action="${name}"]`),mic=find('mic'),cancel=find('cancel'),file=find('file'),upload=find('upload'),auto=find('auto'),stop=find('stop'),note=bar.querySelector('[role=status]');
  const read=document.createElement('button');read.type='button';read.textContent='朗读';read.setAttribute('aria-label','朗读最近一条回答');
  responseCard.querySelector('header').insertBefore(read,responseCard.querySelector('#web-agent-clear'));
  let latest='',revision=null,capture=null,micSequence=0,transcribing=false,starting=false,speaking=false,speechSequence=0,fetchController=null,asrController=null,audioUrl=null,playbackFinish=null;
  const audio=new Audio();audio.preload='auto';
  try{auto.checked=localStorage.getItem('smartport-auto-speech')==='1';}catch{}
  const say=text=>{note.textContent=text;};
  function controls(){mic.textContent=capture&&!starting?'停止录音':'语音输入';mic.setAttribute('aria-pressed',String(Boolean(capture&&!starting)));mic.disabled=starting||transcribing;file.disabled=Boolean(capture)||starting||transcribing;cancel.hidden=!(capture||starting||transcribing);stop.disabled=!speaking;read.disabled=!latest;}
  function stopSpeech(){++speechSequence;fetchController?.abort();fetchController=null;audio.pause();audio.removeAttribute('src');audio.load();playbackFinish?.();playbackFinish=null;if(audioUrl){URL.revokeObjectURL(audioUrl);audioUrl=null;}speaking=false;controls();}
  function cleanup(record){clearTimeout(record?.timer);record?.stream?.getTracks().forEach(t=>t.stop());try{record?.source?.disconnect();record?.node?.disconnect();}catch{}record?.context?.close().catch(()=>{});}
  async function jsonError(response){try{const data=await response.json();return data.detail||data.error||'语音处理失败';}catch{return '语音服务暂不可用';}}
  async function transcribe(blob){
    const sequence=++micSequence;transcribing=true;asrController=new AbortController();controls();say('正在本机识别…');
    try{
      const response=await fetch('/api/speech/asr',{method:'POST',headers:{'Content-Type':'audio/wav'},body:blob,signal:asrController.signal});
      if(!response.ok)throw new Error(await jsonError(response));const data=await response.json();
      if(sequence!==micSequence)return;
      if(!data.text?.trim()){say('没有识别到语音，请重试');return;}
      input.value+=(input.value&&!/\s$/.test(input.value)?' ':'')+data.text.trim();onDraft();input.focus();say('已填入文字，请检查后点击 Send');
    }catch(error){if(error.name!=='AbortError')say(error.message);}
    finally{if(sequence===micSequence){transcribing=false;asrController=null;controls();}}
  }
  async function finishRecord(){
    const record=capture;if(!record||starting)return;starting=true;controls();
    try{
      clearTimeout(record.timer);await Promise.race([new Promise(resolve=>{record.finished=resolve;record.node.port.postMessage('stop');}),new Promise(resolve=>setTimeout(resolve,500))]);
      if(capture!==record)return;capture=null;cleanup(record);starting=false;controls();
      const duration=record.chunks.reduce((n,c)=>n+c.length,0)/record.context.sampleRate;
      if(duration<.25){say('录音太短，请说话后再停止');return;}
      await transcribe(encodeWav(record.chunks,record.context.sampleRate));
    }catch(error){cleanup(record);if(capture===record)capture=null;starting=false;controls();say(error.message);}
  }
  async function startRecord(){
    if(!isSecureContext||!navigator.mediaDevices?.getUserMedia){say('麦克风需要 HTTPS 或本机 localhost；也可点击“识别音频”上传 WAV');return;}
    stopSpeech();const sequence=++micSequence;starting=true;const record={chunks:[]};capture=record;controls();say('请允许麦克风权限…');
    try{
      record.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
      if(sequence!==micSequence){cleanup(record);return;}
      record.context=new AudioContext();await record.context.resume();await record.context.audioWorklet.addModule('/static/smartport-recorder.js?v=1');
      if(sequence!==micSequence){cleanup(record);return;}
      record.node=new AudioWorkletNode(record.context,'smartport-recorder');record.source=record.context.createMediaStreamSource(record.stream);
      record.node.port.onmessage=e=>{if(capture!==record)return;if(e.data.type==='chunk')record.chunks.push(e.data.samples);else if(e.data.type==='stopped')record.finished?.();};
      record.source.connect(record.node);record.node.connect(record.context.destination);
      starting=false;controls();say('正在录音，再点一次停止（最长60秒）');record.timer=setTimeout(finishRecord,59000);
    }catch(error){cleanup(record);if(sequence===micSequence){capture=null;starting=false;controls();say(error.name==='NotAllowedError'?'未获得麦克风权限，请在浏览器站点设置中允许':error.name==='NotFoundError'?'未检测到麦克风':error.message);}}
  }
  function cancelRecord(){++micSequence;cleanup(capture);capture=null;asrController?.abort();asrController=null;transcribing=false;starting=false;controls();say('已取消录音或识别');}
  async function speak(text){
    stopSpeech();if(capture||transcribing){say('请先完成录音或识别');return;}
    const sequence=speechSequence;let parts;
    try{parts=spokenChunks(text);}catch(error){say(error.message);return;}
    if(!parts.length){say('没有可朗读的文字');return;}
    speaking=true;controls();
    try{
      for(let i=0;i<parts.length;i++){
        if(sequence!==speechSequence)return;say(`正在合成语音 ${i+1}/${parts.length}…`);fetchController=new AbortController();
        const response=await fetch('/api/speech/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:parts[i]}),signal:fetchController.signal});
        if(!response.ok)throw new Error(await jsonError(response));const blob=await response.blob();
        if(sequence!==speechSequence)return;
        if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(blob);audio.src=audioUrl;say(`正在朗读 ${i+1}/${parts.length}`);
        await new Promise((resolve,reject)=>{playbackFinish=resolve;audio.onended=resolve;audio.onerror=()=>reject(new Error('音频播放失败'));audio.play().catch(()=>reject(new Error('浏览器阻止了自动播放，请点击“朗读”')));});
      }
      if(sequence===speechSequence)say('朗读完成');
    }catch(error){if(sequence===speechSequence&&error.name!=='AbortError')say(error.message);}
    finally{if(sequence===speechSequence)stopSpeech();}
  }
  mic.onclick=()=>capture?finishRecord():startRecord();cancel.onclick=cancelRecord;stop.onclick=()=>{stopSpeech();say('已停止朗读');};read.onclick=()=>speak(latest);
  file.onclick=()=>upload.click();upload.onchange=()=>{const selected=upload.files[0];upload.value='';if(!selected)return;if(selected.size>12*1024*1024){say('音频文件不能超过12MB');return;}stopSpeech();transcribe(selected);};
  auto.onchange=()=>{try{localStorage.setItem('smartport-auto-speech',auto.checked?'1':'0');}catch{}if(!auto.checked)stopSpeech();};
  window.addEventListener('pagehide',()=>{cancelRecord();stopSpeech();});
  controls();
  fetch('/api/speech/health',{cache:'no-store'}).then(async r=>{if(!r.ok)throw new Error(await jsonError(r));if(!capture&&!transcribing&&!speaking)say(isSecureContext?'本地语音已就绪':'局域网HTTP无法使用麦克风，可上传WAV音频');}).catch(e=>say(e.message));
  return {stop:stopSpeech,update(state){
    const last=(state.chat||[]).at(-1),count=state.chat_count??JSON.stringify(state.chat||[]);
    latest=[...(state.chat||[])].reverse().find(m=>m.speaker==='Agent')?.text||'';read.disabled=!latest;
    if(state.busy||capture||transcribing)return;
    if(revision!==null&&count!==revision&&auto.checked&&last?.speaker==='Agent'&&(state.chat||[]).some(m=>m.speaker==='You'))speak(last.text);
    revision=count;
  }};
}
