// Model-view controls run before the SDK's document handlers, including when
// its transparent cursor layer (rather than the video) receives the event.
if(document.documentElement.classList.contains('smartport-video-only')) {
  const video = document.getElementById('remote-video');
  const bar = document.createElement('aside'); bar.id = 'web-navigation';
  bar.innerHTML = `<button type="button" aria-label="恢复港区视角">恢复视角</button><span title="左键拖动：环绕；中键或 Shift+左键：平移；滚轮或 +/-：缩放；方向键或 WASD：平移；Home：复位。">左键环绕 · 中键平移 · 滚轮缩放</span><output aria-live="polite"></output>`;
  document.body.append(bar);
  const output = bar.querySelector('output');
  video.tabIndex = 0;
  video.setAttribute('aria-label', '港区模型视图');
  let drag = null, focused = false, pending = {}, timer = null, busy = false;
  function consume(e) {e.preventDefault(); e.stopImmediatePropagation();}
  function editing(e) {
    return e.target?.closest?.('input,textarea,select,[contenteditable="true"],#web-agent,#web-agent-settings,#web-navigation');
  }
  function scenePoint(e) {
    if(editing(e) || !document.getElementById('web-agent-settings')?.hidden) return false;
    const r = video.getBoundingClientRect();
    if(!r.width || !r.height || e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return false;
    const w = video.videoWidth || 2560, h = video.videoHeight || 1440;
    const x = (e.clientX-r.left)*w/r.width, y = (e.clientY-r.top)*h/r.height;
    // The streamed Kit cards retain their own buttons and scrolling.
    const m=Math.min(14,w/20,h/20), available=w-2*m;
    const iw=Math.min(560,Math.max(280,w*.3),available), ih=Math.min(100,h-2*m), iy=h-m-ih;
    const ow=Math.min(320,Math.max(240,w*.18),available), ox=w-m-ow;
    let oh=Math.min(340,Math.max(190,h*.4),h-2*m), oy=h-m-oh;
    if((w+iw)/2+m>ox){oh=Math.max(0,Math.min(oh,iy-2*m));oy=Math.max(m,iy-m-oh);}
    return y>44 && !(x>=ox && x<=w-m && y>=oy && y<=oy+oh) &&
      !(x>=(w-iw)/2 && x<=(w+iw)/2 && y>=iy && y<=iy+ih);
  }
  function enqueue(change) {
    if(change.action === 'reset') pending={action:'reset'};
    else for(const [key,value] of Object.entries(change)) pending[key]=Math.max(-180,Math.min(180,(pending[key]||0)+value));
    if(!timer && !busy) timer=setTimeout(flush,24);
  }
  async function flush() {
    timer=null; if(busy || !Object.keys(pending).length) return;
    busy=true; const command=pending; pending={};
    // Do not replay a large accumulated wheel burst after a slow response.
    if(command.zoom) command.zoom=Math.max(-3,Math.min(3,command.zoom));
    try {
      const response=await fetch('/api/smartport-agent/navigate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});
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
    if(!scenePoint(e)){focused=false;return;}
    consume(e);focused=true;video.focus({preventScroll:true});
    if(e.button!==0 && e.button!==1) return;
    drag={id:e.pointerId,x:e.clientX,y:e.clientY,pan:e.button===1 || e.shiftKey};
    video.setPointerCapture(e.pointerId);
  },true);
  window.addEventListener('pointermove', e=>{
    if(!drag || drag.id!==e.pointerId) return;
    consume(e);
    const scale=1000/Math.max(400,video.getBoundingClientRect().width);
    const dx=(e.clientX-drag.x)*scale,dy=(e.clientY-drag.y)*scale;
    drag.x=e.clientX;drag.y=e.clientY;
    enqueue(drag.pan?{pan_x:dx,pan_y:dy}:{orbit_x:dx,orbit_y:dy});
  },true);
  function endDrag(e) {
    if(!drag || drag.id!==e.pointerId) return;
    consume(e);drag=null;
    if(video.hasPointerCapture(e.pointerId)) video.releasePointerCapture(e.pointerId);
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
    if(!focused || editing(e) || !document.getElementById('web-agent-settings')?.hidden) return;
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
