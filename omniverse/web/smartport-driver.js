// Keys terminate here; the streaming SDK never receives driving/navigation input.
import {startJpegFrames} from './smartport-jpeg.js';
if (document.documentElement.classList.contains('smartport-driver') && !document.documentElement.classList.contains('smartport-rtg-driver') && !document.documentElement.classList.contains('smartport-sts-driver')) {
  const style=document.createElement('style');
  style.textContent=`#driver-hud{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:10020;width:min(650px,90vw);box-sizing:border-box;padding:18px 24px;background:#10202feb;border:1px solid #456278;border-radius:14px;color:#edf5ff;font:14px system-ui;box-shadow:0 12px 40px #0006}#driver-hud header{display:flex;gap:18px;align-items:center}#driver-hud strong{font-size:23px;font-variant-numeric:tabular-nums}#driver-hud p{margin:9px 0 0;color:#aac1d5}#driver-hud button{padding:7px 12px;margin-left:auto;background:#244b68;border:1px solid #5c7f99;border-radius:6px;color:white;cursor:pointer}#driver-hud output{display:block;min-height:21px;margin-top:8px;color:#ffd08a}#web-agent,#web-agent-response,#web-navigation,#transfer-cinema{display:none!important}`;
  style.textContent+=`
    #driver-hud .driver-settings { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:9px; }
    #driver-hud .picture-quality { margin:0; }
    #driver-hud .collision-switch:not([hidden]) { display:inline-flex; align-items:center; gap:8px; white-space:nowrap; background:transparent; }
    .collision-switch .switch-track { width:32px; height:18px; border-radius:9px; background:#506780; display:inline-flex; align-items:center; padding:2px; box-sizing:border-box; }
    .collision-switch .switch-track::before { content:''; width:14px; height:14px; border-radius:50%; background:white; transition:transform .15s; }
    .collision-switch[aria-checked="true"] .switch-track { background:#11836e; }
    .collision-switch[aria-checked="true"] .switch-track::before { transform:translateX(14px); }
  `;
  document.head.append(style);
  const hud=document.createElement('aside');hud.id='driver-hud';
  hud.innerHTML='<header><span>01号集卡 · <b class="view">驾驶室</b></span><strong>0.0 km/h</strong><button type="button" class="view-toggle">切换视角 V</button><button type="button" class="reset">复位 R</button></header><p>W / ↑ 前进　S / ↓ 后退　A D / ← → 转向　空格 刹车</p><div class="driver-settings"><p class="picture-quality"></p><button type="button" class="collision-switch" role="switch" aria-checked="false" hidden>碰撞检测<span class="switch-track" aria-hidden="true"></span></button></div><output role="status">正在进入驾驶模式…</output><button type="button" class="retry" hidden>重新进入驾驶</button>';
  document.body.append(hud);
  const video=document.getElementById('remote-video');if(video){video.tabIndex=0;video.setAttribute('aria-label','01号集卡驾驶画面');video.focus();}
  const keys=new Set();let active=false,busy=false,view='cabin',reset=false,closed=false,seq=0,jpegView=null;
  let collisionEnabled=false,hasCollisionSwitch=false;
  const clientId=Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
  let lastVideoFrame=0,frameWindowStart=performance.now(),frameCount=0,videoFps=0;
  if(video?.requestVideoFrameCallback){
    const frame=()=>{
      lastVideoFrame=performance.now();frameCount++;
      if(lastVideoFrame-frameWindowStart>=1000){videoFps=frameCount*1000/(lastVideoFrame-frameWindowStart);frameCount=0;frameWindowStart=lastVideoFrame;}
      if(!closed)video.requestVideoFrameCallback(frame);
    };
    video.requestVideoFrameCallback(frame);
  }
  function pictureReady(){return jpegView?jpegView.ready():!!video&&video.readyState>=2&&!video.paused&&(!video.requestVideoFrameCallback||performance.now()-lastVideoFrame<750);}
  const output=hud.querySelector('output'),retry=hud.querySelector('.retry');
  const collisionSwitch=hud.querySelector('.collision-switch');
  function render(state){
    hud.querySelector('.view').textContent=(state.view||view)==='cabin'?'驾驶室':'车外跟随';
    hud.querySelector('strong').textContent=`${state.speed_kmh<0?'倒挡 ':''}${Math.abs(state.speed_kmh||0).toFixed(1)} km/h`;
    hud.querySelector('.picture-quality').textContent=video?.videoWidth?
      `${video.videoWidth} × ${video.videoHeight}${video.requestVideoFrameCallback?' · '+(pictureReady()?videoFps:0).toFixed(0)+' FPS':''}`:'';
    if(!pictureReady())output.textContent='正在等待驾驶画面，车辆保持制动…';
    else if(state.startup_phase==='collision_index')output.textContent='正在准备碰撞检测，车辆保持制动…';
    else output.textContent=state.notice||(state.collision_enabled===false?'碰撞检测已关闭，落海自动复位。':'碰撞会停车，落海自动复位。');
  }
  async function api(action,data={}){
    data={...data,client_id:clientId};
    if(action==='input')data={...data,seq:seq++};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),action==='acquire'?60000:8000);
    try{
      const response=await fetch('/api/truck-driver/'+action+(action==='state'?'?client_id='+clientId:''),
        action==='state'?{method:'GET',cache:'no-store',signal:controller.signal}:
        {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),signal:controller.signal});
      if(response.status===401){location.replace('/login');throw new Error('登录已失效');}
      const state=await response.json();if(!response.ok){const error=new Error(state.detail||'暂时无法驾驶');error.retryable=response.status===503;throw error;}return state;
    }catch(error){
      if(controller.signal.aborted){const timeout=new Error(action==='acquire'?'申请驾驶超时，Kit仍未响应，请稍后重试':'驾驶服务响应超时，车辆保持制动');timeout.retryable=true;throw timeout;}
      throw error;
    }finally{clearTimeout(timer);}
  }
  async function acquire(){
    if(busy||closed)return;busy=true;retry.hidden=true;collisionSwitch.hidden=true;
    try{
      const state=await api('acquire');active=true;seq=Math.max(seq,(state.input_seq??-1)+1);view=state.view||view;render(state);
      let status=state;const deadline=performance.now()+120000;
      while(status.stream_ready===false){
        if(closed)return;
        if(performance.now()>deadline)throw new Error('独立驾驶画面启动超时，请重新进入驾驶');
        await new Promise(resolve=>setTimeout(resolve,250));
        output.textContent=status.startup_phase==='collision_index'?`正在准备碰撞检测（已检查 ${status.collision_scanned||0} 项），车辆保持制动…`:'正在启动司机独立画面，车辆保持制动…';
        try{status=await api('state');}catch(error){if(error.retryable)continue;throw error;}
        if(status.status==='idle')throw new Error('驾驶会话已结束，请重新进入');
      }
      if(status.stream?.transport==='jpeg')jpegView=startJpegFrames('truck-driver',clientId);
      hasCollisionSwitch=typeof status.collision_enabled==='boolean';
      collisionEnabled=status.collision_enabled===true;
      collisionSwitch.setAttribute('aria-checked',String(collisionEnabled));
      collisionSwitch.hidden=!hasCollisionSwitch;
      window.smartportDriverTransport=status.stream?.transport||'webrtc';
      window.smartportDriverReady=true;window.dispatchEvent(new Event('smartport-driver-ready'));
    }catch(error){active=false;keys.clear();output.textContent=error.message;retry.hidden=false;}finally{busy=false;}
  }
  async function flush(){
    if(!active||busy||closed)return;busy=true;
    const pressed=(...codes)=>codes.some(code=>keys.has(code));
    const command={throttle:Number(pressed('KeyW','ArrowUp'))-Number(pressed('KeyS','ArrowDown')),
                   steer:Number(pressed('KeyA','ArrowLeft'))-Number(pressed('KeyD','ArrowRight')),brake:pressed('Space'),view,reset};
    if(hasCollisionSwitch)command.collision_enabled=collisionEnabled;
    if(!pictureReady()){command.throttle=0;command.steer=0;command.brake=true;keys.clear();}
    reset=false;
    try{const state=await api('input',command);if(state.status==='idle'){active=false;keys.clear();collisionSwitch.hidden=true;retry.hidden=false;output.textContent='驾驶会话已结束，请重新进入。';}else render(state);}
    catch(error){keys.clear();output.textContent='连接中断，车辆已停止。'+error.message;}
    finally{busy=false;}
  }
  function consume(event){event.preventDefault();event.stopImmediatePropagation();}
  function toggle(){view=view==='cabin'?'follow':'cabin';void flush();}
  hud.querySelector('.view-toggle').onclick=toggle;
  collisionSwitch.onclick=()=>{
    collisionEnabled=!collisionEnabled;
    collisionSwitch.setAttribute('aria-checked',String(collisionEnabled));
    void flush();
  };
  hud.querySelector('.reset').onclick=()=>{keys.clear();reset=true;void flush();};retry.onclick=acquire;
  for(const name of ['keydown','keyup'])window.addEventListener(name,event=>{
    if(event.ctrlKey||event.metaKey||event.altKey||event.key==='F11'||event.key==='Escape'){event.stopImmediatePropagation();return;}
    if(event.target.closest?.('input,textarea,select,[contenteditable="true"]')){event.stopImmediatePropagation();return;}
    if(event.key==='Tab'||(event.target.closest?.('button,a')&&['Enter',' '].includes(event.key))){event.stopImmediatePropagation();return;}
    consume(event);
    if(name==='keyup')keys.delete(event.code);
    else if(!event.repeat&&event.code==='KeyV')toggle();
    else if(!event.repeat&&event.code==='KeyR'){keys.clear();reset=true;}
    else keys.add(event.code);
    void flush();
  },true);
  for(const name of ['pointerdown','pointerup','pointermove','mousedown','mouseup','mousemove','wheel','dblclick','contextmenu'])
    window.addEventListener(name,event=>{if(!event.target.closest?.('#driver-hud,form,button,a,input'))consume(event);}, {capture:true,passive:false});
  function stop(){keys.clear();void api('input',{throttle:0,steer:0,brake:true,view}).catch(()=>{});}
  window.addEventListener('blur',stop);
  for(const event of ['waiting','emptied','pause','error'])video?.addEventListener(event,stop);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('pagehide',()=>{closed=true;keys.clear();jpegView?.close();navigator.sendBeacon('/api/truck-driver/release',JSON.stringify({client_id:clientId}));});
  setInterval(flush,100);void acquire();
}
