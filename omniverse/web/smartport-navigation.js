import {createSceneInspector} from './smartport-device-map.js?v=4';

// Model-view controls run before the SDK's document handlers, including when
// its transparent cursor layer (rather than the video) receives the event.
const pageClasses = document.documentElement.classList;
const iecsView = pageClasses.contains('smartport-iecs');
if(pageClasses.contains('smartport-video-only') && (!pageClasses.contains('smartport-spectator') || iecsView) && !pageClasses.contains('smartport-driver')) {
  const video = document.getElementById('remote-video');
  const bar = document.createElement('aside'); bar.id = 'web-navigation';
  bar.innerHTML = `<button type="button" aria-label="恢复港区视角">恢复视角</button><span title="左键拖动：环绕；中键或 Shift+左键：平移；滚轮或 +/-：缩放；方向键或 WASD：平移；Home：复位。">左键环绕 · 中键平移 · 滚轮缩放</span><output aria-live="polite"></output>`;
  document.body.append(bar);
  const output = bar.querySelector('output');
  video.tabIndex = 0;
  video.setAttribute('aria-label', '港区模型视图');
  let drag = null, focused = false, pending = {}, timer = null, busy = false;
  const api = iecsView ? '/api/iecs' : '/api/smartport-agent';
  const inspector = createSceneInspector(api);
  function consume(e) {e.preventDefault(); e.stopImmediatePropagation();}
  function editing(e) {
    return e.target?.closest?.('input,textarea,select,[contenteditable="true"],#web-agent,#web-agent-settings,#web-agent-response,#web-live-camera,#web-sts-statistics,#web-navigation,#web-container-info,#web-device-map,#web-device-map-toggle,#transfer-cinema,#iecs-workbench,#spectator-fullscreen,form[action="/auth/logout"]');
  }
  function settingsOpen() {
    const settings = document.getElementById('web-agent-settings');
    return settings && !settings.hidden;
  }
  function scenePoint(e) {
    if(editing(e) || settingsOpen()) return false;
    const r = video.getBoundingClientRect();
    if(!r.width || !r.height || e.clientX<r.left || e.clientX>=r.right || e.clientY<r.top || e.clientY>=r.bottom) return false;
    const w = video.videoWidth || 2560, h = video.videoHeight || 1440;
    const scale=(iecsView ? Math.max : Math.min)(r.width/w,r.height/h), width=w*scale, height=h*scale;
    const left=r.left+(r.width-width)/2, top=r.top+(r.height-height)/2;
    const point={x:(e.clientX-left)/width,y:(e.clientY-top)/height};
    if(point.x<0 || point.x>=1 || point.y<0 || point.y>=1) return false;
    if(iecsView) return point;
    const x=point.x*w, y=point.y*h;
    // The streamed Kit cards retain their own buttons and scrolling.
    const m=Math.min(14,w/20,h/20), available=w-2*m;
    const iw=Math.min(560,Math.max(280,w*.3),available), ih=Math.min(100,h-2*m), iy=h-m-ih;
    const ow=Math.min(320,Math.max(240,w*.18),available), ox=w-m-ow;
    let oh=Math.min(340,Math.max(190,h*.4),h-2*m), oy=h-m-oh;
    if((w+iw)/2+m>ox){oh=Math.max(0,Math.min(oh,iy-2*m));oy=Math.max(m,iy-m-oh);}
    return y>44 && !(x>=ox && x<=w-m && y>=oy && y<=oy+oh) &&
      !(x>=(w-iw)/2 && x<=(w+iw)/2 && y>=iy && y<=iy+ih) && point;
  }
  function enqueue(change) {
    if(change.action === 'reset') pending={action:'reset'};
    else for(const [key,value] of Object.entries(change)) {
      const limit=key.startsWith('pan_')?100:180;
      pending[key]=Math.max(-limit,Math.min(limit,(pending[key]||0)+value));
    }
    if(!timer && !busy) timer=setTimeout(flush,24);
  }
  async function flush() {
    timer=null; if(busy || !Object.keys(pending).length) return;
    busy=true; const command=pending; pending={};
    // Do not replay a large accumulated wheel burst after a slow response.
    if(command.zoom) command.zoom=Math.max(-3,Math.min(3,command.zoom));
    try {
      const response=await fetch(`${api}/navigate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});
      const state=await response.json(); if(!response.ok) throw new Error(state.detail || '视角操作暂不可用');
      output.textContent=`距离 ${Math.round(state.distance_m)} m`;
      output.dataset.distance=state.distance_m; output.dataset.height=state.height_m; output.dataset.pitch=state.pitch_deg;
      output.removeAttribute('data-error');
    } catch(e) {pending={};output.textContent=e.message;output.dataset.error='true';}
    finally {busy=false;if(Object.keys(pending).length) timer=setTimeout(flush,24);}
  }
  bar.querySelector('button').onclick=()=>{focused=true;video.focus({preventScroll:true});enqueue({action:'reset'});};
  window.addEventListener('wheel', e=>{
    if(!scenePoint(e)) return;
    consume(e); focused=true;
    const r=video.getBoundingClientRect();
    const pixels=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?r.height:1);
    enqueue({zoom:-Math.max(-2,Math.min(2,pixels/100))});
  },{capture:true,passive:false});
  window.addEventListener('pointerdown', e=>{
    if(!scenePoint(e)){
      focused=false;
      if(inspector && e.button===0 && !editing(e) && !settingsOpen()) inspector.dismiss();
      return;
    }
    consume(e);focused=true;video.focus({preventScroll:true});
    if(e.button!==0 && e.button!==1) return;
    drag={id:e.pointerId,x:e.clientX,y:e.clientY,pan:e.button===1 || e.shiftKey,moved:false,
      pick:inspector && e.button===0 && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey};
    video.setPointerCapture(e.pointerId);
  },true);
  window.addEventListener('pointermove', e=>{
    if(!drag || drag.id!==e.pointerId) return;
    consume(e);
    if(!drag.moved && Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<5) return;
    drag.moved=true;
    const scale=1000/Math.max(400,video.getBoundingClientRect().width);
    const dx=(e.clientX-drag.x)*scale,dy=(e.clientY-drag.y)*scale;
    drag.x=e.clientX;drag.y=e.clientY;
    enqueue(drag.pan?{pan_x:dx,pan_y:dy}:{orbit_x:dx,orbit_y:dy});
  },true);
  function endDrag(e) {
    if(!drag || drag.id!==e.pointerId) return;
    const point = e.type==='pointerup' && drag.pick && !drag.moved &&
      Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<5 && scenePoint(e);
    consume(e);drag=null;
    if(video.hasPointerCapture(e.pointerId)) video.releasePointerCapture(e.pointerId);
    if(point && video.videoWidth) inspector.pick(point);
  }
  window.addEventListener('pointerup',endDrag,true);
  window.addEventListener('pointercancel',endDrag,true);
  window.addEventListener('lostpointercapture',()=>{drag=null;},true);
  // Block legacy mouse events as well; otherwise Kit can also rotate/pan.
  for(const name of ['mousedown','mouseup','auxclick','dblclick','contextmenu'])
    window.addEventListener(name,e=>{if(drag || scenePoint(e)) consume(e);},true);
  window.addEventListener('mousemove',e=>{if(drag) consume(e);},true);
  window.addEventListener('blur',()=>{drag=null;focused=false;});
  const handledKeys=new Set(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','w','a','s','d','W','A','S','D','+','=','-','_','Home']);
  window.addEventListener('keydown',e=>{
    if(!focused || editing(e) || settingsOpen()) return;
    // Preserve browser shortcuts, and isolate all other keys from free flight.
    if((e.ctrlKey || e.metaKey || e.altKey) && !['+','=','-','_'].includes(e.key)) {e.stopImmediatePropagation();return;}
    if(!handledKeys.has(e.key)){e.stopImmediatePropagation();return;}
    consume(e);const key=e.key.toLowerCase(),step=e.shiftKey?22:10;
    if(e.key==='Home') enqueue({action:'reset'});
    else if(['+','='].includes(key)) enqueue({zoom:.6});
    else if(['-','_'].includes(key)) enqueue({zoom:-.6});
    else enqueue({pan_x:['a','arrowleft'].includes(key)?step:['d','arrowright'].includes(key)?-step:0,
                  pan_y:['w','arrowup'].includes(key)?step:['s','arrowdown'].includes(key)?-step:0});
  },true);
  window.addEventListener('keyup',e=>{if(focused && !editing(e)) e.stopImmediatePropagation();},true);
}
