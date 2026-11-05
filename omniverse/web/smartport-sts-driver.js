// This page sends only STS commands; native streaming input remains disabled.
if (document.documentElement.classList.contains('smartport-sts-driver')) {
  const style=document.createElement('style');
  style.textContent=`#sts-driver-hud{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:10020;width:min(900px,94vw);box-sizing:border-box;padding:16px 22px;background:#10202feb;border:1px solid #456278;border-radius:14px;color:#edf5ff;font:14px system-ui}#sts-driver-hud header{display:flex;flex-wrap:wrap;gap:12px;align-items:center}#sts-driver-hud strong{font-size:18px;font-variant-numeric:tabular-nums}#sts-driver-hud p{margin:9px 0 0;color:#aac1d5}#sts-driver-hud button{padding:7px 12px;background:#244b68;border:1px solid #5c7f99;border-radius:6px;color:white;cursor:pointer}#sts-driver-hud output{display:block;min-height:21px;margin-top:8px;color:#ffd08a}#web-agent,#web-agent-response,#web-navigation,#transfer-cinema{display:none!important}`;
  document.head.append(style);
  const hud=document.createElement('aside');hud.id='sts-driver-hud';
  hud.innerHTML='<header><span>STS · <b class="view">吊具下方</b></span><strong class="fov">140°</strong><button type="button" class="zoom">视野角 F</button><button type="button" class="view-toggle">切换视角 V</button></header><p>W / S 大车　A / D 小车　Q / E 升降　空格 停止</p><p class="tools"><button type="button" class="size20">20尺 1</button> <button type="button" class="size40">40尺 2</button> <button type="button" class="lock">闭锁 K</button> <button type="button" class="unlock">开锁 U</button> <span class="spreader"></span></p><p class="position"></p><output role="status">正在进入STS驾驶模式…</output><button type="button" class="retry" hidden>重新进入驾驶</button>';
  document.body.append(hud);
  const video=document.getElementById('remote-video');
  if(video){video.tabIndex=0;video.setAttribute('aria-label','STS司机独立画面');video.focus();}
  const keys=new Set(),views=['under','side','end'],labels={under:'吊具下方',side:'吊具侧向',end:'吊具端向'};
  let active=false,busy=false,closed=false,seq=0,view='under',fov=140,lastVideoFrame=0,focused=true;
  const pending=[];
  const clientId=Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
  if(video?.requestVideoFrameCallback){
    const frame=()=>{lastVideoFrame=performance.now();if(!closed)video.requestVideoFrameCallback(frame);};video.requestVideoFrameCallback(frame);
  }
  const output=hud.querySelector('output'),retry=hud.querySelector('.retry');
  function pictureReady(){return focused&&!document.hidden&&!!video&&video.readyState>=2&&!video.paused&&(!video.requestVideoFrameCallback||performance.now()-lastVideoFrame<750);}
  function render(state){
    hud.querySelector('.view').textContent=labels[state.view||view];
    hud.querySelector('.fov').textContent=`${state.fov??fov}°`;
    hud.querySelector('.position').textContent=`大车 ${(state.gantry_m||0).toFixed(2)} m　小车 ${(state.trolley_m||0).toFixed(2)} m　起升 ${(state.hoist_m||0).toFixed(2)} m`;
    hud.querySelector('.spreader').textContent=`${state.spreader_size_ft==null?'尺型未知':state.spreader_size_ft.toFixed(1)+'尺'} · ${state.locked?'已闭锁':'已开锁'}`;
    for(const selector of ['.size20','.size40'])hud.querySelector(selector).disabled=!!state.locked;
    hud.querySelector('.lock').disabled=!!state.locked;hud.querySelector('.unlock').disabled=!state.locked;
    output.textContent=state.notice||(!pictureReady()?'正在等待STS画面，设备保持停止…':'闭锁需四角对准箱顶；开锁前箱子须落稳。');
  }
  async function api(action,data={}){
    data={...data,client_id:clientId};if(action==='input')data.seq=seq++;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),action==='acquire'?60000:8000);
    try{
      const response=await fetch('/api/sts-driver/'+action+(action==='state'?'?client_id='+clientId:''),
        action==='state'?{method:'GET',cache:'no-store',signal:controller.signal}:
        {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),signal:controller.signal});
      if(response.status===401){location.replace('/login');throw new Error('登录已失效');}
      const state=await response.json();
      if(!response.ok){const error=new Error(state.detail||'暂时无法操作STS');error.status=response.status;error.retryable=response.status===503;throw error;}return state;
    }catch(error){
      if(controller.signal.aborted){const timeout=new Error('STS服务响应超时，设备保持停止，请稍后重试');timeout.retryable=true;throw timeout;}throw error;
    }finally{clearTimeout(timer);}
  }
  async function acquire(){
    if(busy||closed)return;busy=true;retry.hidden=true;
    try{
      let state=await api('acquire');active=true;seq=Math.max(seq,(state.input_seq??-1)+1);view=state.view||view;fov=state.fov??fov;render(state);
      const deadline=performance.now()+120000;
      while(state.stream_ready===false){
        if(closed)return;if(performance.now()>deadline)throw new Error('STS独立画面启动超时，请重新进入');
        output.textContent='正在启动STS独立画面，设备保持停止…';
        await new Promise(resolve=>setTimeout(resolve,250));
        try{state=await api('state');}catch(error){if(error.retryable)continue;throw error;}
        if(state.status==='idle')throw new Error('STS驾驶会话已结束，请重新进入');
      }
      window.smartportDriverReady=true;window.dispatchEvent(new Event('smartport-driver-ready'));
    }catch(error){active=false;keys.clear();output.textContent=error.message;retry.hidden=false;}finally{busy=false;}
  }
  async function flush(){
    if(!active||busy||closed)return;busy=true;
    const pressed=code=>Number(keys.has(code));
    const command={gantry:pressed('KeyW')-pressed('KeyS'),trolley:pressed('KeyD')-pressed('KeyA'),
      hoist:pressed('KeyQ')-pressed('KeyE'),brake:keys.has('Space'),view,fov};
    if(!pictureReady()||command.brake){command.brake=true;keys.clear();pending.length=0;}
    else if(pending.length)Object.assign(command,pending.shift());
    try{
      const state=await api('input',command);
      if(state.status==='idle'){active=false;keys.clear();retry.hidden=false;output.textContent='STS驾驶会话已结束，请重新进入。';}else render(state);
    }catch(error){keys.clear();output.textContent=(error.status===400?'操作未完成：':'连接中断，STS已停止。')+error.message;}finally{busy=false;}
  }
  function zoom(){fov=fov<=10?140:fov-10;void flush();}
  function toggle(){view=views[(views.indexOf(view)+1)%views.length];void flush();}
  function tool(command){if(!active||closed||!pictureReady())return;keys.clear();pending.length=0;pending.push(command);void flush();}
  hud.querySelector('.zoom').onclick=zoom;hud.querySelector('.view-toggle').onclick=toggle;retry.onclick=acquire;
  hud.querySelector('.size20').onclick=()=>tool({size_ft:20});hud.querySelector('.size40').onclick=()=>tool({size_ft:40});
  hud.querySelector('.lock').onclick=()=>tool({lock:true});hud.querySelector('.unlock').onclick=()=>tool({lock:false});
  function consume(event){event.preventDefault();event.stopImmediatePropagation();}
  for(const name of ['keydown','keyup'])window.addEventListener(name,event=>{
    if(event.ctrlKey||event.metaKey||event.altKey||event.key==='F11'||event.key==='Escape'){event.stopImmediatePropagation();return;}
    if(event.target.closest?.('input,textarea,select,[contenteditable="true"]')){event.stopImmediatePropagation();return;}
    if(event.key==='Tab'||(event.target.closest?.('button,a')&&event.key==='Enter')){event.stopImmediatePropagation();return;}
    consume(event);
    if(name==='keyup')keys.delete(event.code);
    else if(!event.repeat&&event.code==='KeyF')zoom();
    else if(!event.repeat&&event.code==='KeyV')toggle();
    else if(!event.repeat&&event.code==='Digit1')tool({size_ft:20});
    else if(!event.repeat&&event.code==='Digit2')tool({size_ft:40});
    else if(!event.repeat&&event.code==='KeyK')tool({lock:true});
    else if(!event.repeat&&event.code==='KeyU')tool({lock:false});
    else keys.add(event.code);
    void flush();
  },true);
  for(const name of ['pointerdown','pointerup','pointermove','mousedown','mouseup','mousemove','wheel','dblclick','contextmenu'])
    window.addEventListener(name,event=>{if(!event.target.closest?.('#sts-driver-hud,form,button,a,input'))consume(event);},{capture:true,passive:false});
  function stop(){keys.clear();pending.length=0;if(active&&!closed)void api('input',{brake:true,view,fov}).catch(()=>{});}
  window.addEventListener('blur',()=>{focused=false;stop();});window.addEventListener('focus',()=>{focused=true;});
  for(const name of ['waiting','emptied','pause','error'])video?.addEventListener(name,stop);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('pagehide',()=>{closed=true;keys.clear();navigator.sendBeacon('/api/sts-driver/release',JSON.stringify({client_id:clientId}));});
  setInterval(flush,100);void acquire();
}
