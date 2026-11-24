// Browser-native IME controls. Scene operations still use the existing Agent.
import {createStsStatisticsPanel} from './smartport-sts-statistics.js?v=3';
import {enablePanelDrag} from './smartport-panel-drag.js?v=1';

const iecsAgent = document.documentElement.classList.contains('smartport-iecs');
if (iecsAgent || (document.documentElement.classList.contains('smartport-video-only') && !document.documentElement.classList.contains('smartport-spectator') && !document.documentElement.classList.contains('smartport-driver'))) {
  const host = document.createElement('section');
  host.id = 'web-agent'; host.hidden = true;
  host.innerHTML = `<header><span>SMART PORT / AGENT</span><button type="button" id="web-agent-open">Settings</button></header>
    <form><input id="web-agent-input" aria-label="Agent 输入" placeholder="输入中文指令…" autocomplete="off"><button id="web-agent-send">Send</button></form>
    <div id="web-agent-status" role="status">Ready</div>`;
  const modal = document.createElement('div'); modal.id = 'web-agent-settings'; modal.hidden = true;
  modal.innerHTML = `<form aria-label="Agent Settings"><h2>Agent Settings</h2>
    <label>Chat Completions URL<input name="endpoint" type="url" required></label>
    <label>Model<input name="model" required></label>
    <label>API Key<input name="api_key" type="password" autocomplete="new-password"></label>
    <p>API Key 仅保留在 Kit 当前会话；留空可保留已有 Key。</p>
    <div role="status"></div><footer><button type="button" id="web-agent-close">Close</button><button>Save</button></footer></form>`;
  const responseCard=document.createElement('section'); responseCard.id='web-agent-response';
  responseCard.innerHTML='<header><span>AGENT RESPONSE</span><button type="button" id="web-agent-clear">Clear</button></header><div class="conversation"></div><div class="approval" hidden><p></p><button type="button" data-approved="true">确认执行</button><button type="button" data-approved="false">取消</button></div>';
  if (iecsAgent) modal.querySelector('p').textContent = '请填写公网 HTTPS 接口。配置保留在本次登录中。同一接口域名和端口下，Key 留空可保留已有值。';
  document.body.append(host, modal, responseCard);
  const statistics = iecsAgent ? null : createStsStatisticsPanel(api);
  const liveCamera = document.createElement('aside');
  liveCamera.id = 'web-live-camera';
  liveCamera.hidden = true;
  liveCamera.setAttribute('aria-labelledby', 'web-live-camera-title');
  liveCamera.innerHTML = `<header><h2 id="web-live-camera-title">实体相机</h2><button type="button">关闭</button></header>
    <div class="camera-feeds"></div>`;
  if (!iecsAgent) document.body.append(liveCamera);
  let cameraRevision = null, dismissedCameraRevision = null, cameraRequests;
  const cameraDrag = iecsAgent ? null : enablePanelDrag(liveCamera, liveCamera.querySelector('header'));
  const cameraGrid = liveCamera.querySelector('.camera-feeds');
  const closeCamera = liveCamera.querySelector('button');

  closeCamera.onclick = async () => {
    const revision = cameraRevision;
    if (!revision) return;
    dismissedCameraRevision = revision;
    renderLiveCameras(null);
    try {
      await api('live-camera/close', {revision});
    } catch (error) {
      if (!cameraRevision && dismissedCameraRevision === revision) {
        localError = `已关闭本页相机预览，服务端关闭失败：${error.message}`;
        status.textContent = localError;
      }
    }
  };

  function renderLiveCameras(preview) {
    if (preview && preview.revision === dismissedCameraRevision) return;
    if (iecsAgent || (preview?.revision ?? null) === cameraRevision) return;
    cameraRequests?.abort();
    cameraRevision = preview?.revision ?? null;
    cameraGrid.replaceChildren();
    liveCamera.hidden = !preview;
    if (!preview) {
      cameraDrag?.cancel();
      return;
    }
    cameraRequests = new AbortController();
    const hasGrid = preview.cameras.length > 1;
    cameraGrid.classList.toggle('four-cameras', hasGrid);
    for (const camera of preview.cameras) {
      const tile = document.createElement('figure');
      tile.innerHTML = '<figcaption></figcaption><div class="camera-screen"><img hidden><span role="status"></span></div>';
      const caption = tile.querySelector('figcaption');
      caption.textContent = `C${camera.number}${camera.location ? ` · ${camera.location}` : ''}`;
      if (hasGrid && (camera.number === 3 || camera.number === 4)) tile.append(caption);
      cameraGrid.append(tile);
      const picture = tile.querySelector('img'), message = tile.querySelector('[role=status]');
      picture.alt = `C${camera.number} 实时画面`;
      message.textContent = camera.configured ? '正在连接 RTSP…' : '未配置 RTSP';
      if (camera.configured) pollCameraFrame(preview.revision, camera.number, picture, message, cameraRequests.signal);
    }
    cameraDrag?.constrain();
  }

  function pollCameraFrame(revision, number, picture, message, signal) {
    const path = `/api/smartport-agent/live-camera/${revision}/${number}`;
    const started = performance.now();
    let frameUrl;
    signal.addEventListener('abort', () => { if (frameUrl) URL.revokeObjectURL(frameUrl); }, {once: true});
    async function pollFrame() {
      if (signal.aborted) return;
      let frame;
      try {
        const response = await fetch(path, {cache: 'no-store', signal});
        if (response.status === 200) frame = await response.blob();
      } catch (error) {
        if (signal.aborted) return;
      }
      if (signal.aborted) return;
      if (frame) {
        if (frameUrl) URL.revokeObjectURL(frameUrl);
        frameUrl = URL.createObjectURL(frame);
        picture.src = frameUrl;
      }
      picture.hidden = !frame;
      message.hidden = !!frame;
      const statusText = performance.now() - started < 5000 ? '正在连接 RTSP…' : '暂未收到 RTSP 画面，正在重连…';
      if (message.textContent !== statusText) message.textContent = statusText;
      setTimeout(pollFrame, frame ? 100 : 500);
    }
    pollFrame();
  }
  responseCard.querySelector('#web-agent-clear').onclick=()=>{speech.stop();api('clear',{}).then(poll).catch(e=>status.textContent=e.message);};
  responseCard.querySelectorAll('[data-approved]').forEach(button=>button.onclick=()=>{
    if(!state?.approval)return;
    api('approval',{id:state.approval.id,approved:button.dataset.approved==='true'}).then(poll).catch(e=>status.textContent=e.message);
  });
  let chatVersion='';
  function renderResponse() {
    const version=JSON.stringify(state.chat || []);
    const conversation=responseCard.querySelector('.conversation');
    if(version!==chatVersion){
      const atEnd=conversation.scrollHeight-conversation.scrollTop-conversation.clientHeight<30;
      conversation.replaceChildren();
      for(const message of state.chat || []) {
        const line=document.createElement('p');line.textContent=`${message.speaker}: ${message.text}`;conversation.append(line);
      }
      if(atEnd)conversation.scrollTop=conversation.scrollHeight;
      chatVersion=version;
    }
    const approval=responseCard.querySelector('.approval');approval.hidden=!state.approval;
    if(state.approval)approval.querySelector('p').textContent=state.approval.summary || `确认场景操作：${state.approval.name} ${JSON.stringify(state.approval.arguments)}`;
  }
  const input = host.querySelector('input'), sendButton = host.querySelector('#web-agent-send');
  const status = host.querySelector('[role=status]'), settings = modal.querySelector('form');
  let composing = false, state = null, pollBusy = false, posting = false, initialized = false, draftTimer, localError = '';
  // Installed before the SDK's document listeners; keep native IME/default
  // browser editing, but do not also send these keystrokes to the 3D viewport.
  for (const name of ['keydown','keyup','keypress','beforeinput','input','compositionstart','compositionupdate','compositionend']) {
    document.addEventListener(name, event => {
      if (host.contains(event.target) || modal.contains(event.target) || responseCard.contains(event.target) || liveCamera.contains(event.target) || statistics?.element.contains(event.target)) event.stopImmediatePropagation();
    }, true);
  }
  // Target listeners perform editing actions because document capture above
  // intentionally isolates SDK events. Use a capture listener registered first
  // on window to handle Enter and composition without cancelling text input.
  window.addEventListener('compositionstart', e => {if(e.target === input) composing = true;}, true);
  window.addEventListener('compositionend', e => {if(e.target === input){composing = false; scheduleDraft();}}, true);
  window.addEventListener('input', e => {if(e.target === input && !e.isComposing){localError=''; scheduleDraft();}}, true);
  window.addEventListener('keydown', e => {
    if(e.target === input && e.key === 'Enter' && !composing && !e.isComposing && e.keyCode !== 229){e.preventDefault(); submit();}
    if(e.key === 'Escape' && !modal.hidden){modal.hidden = true; input.focus();}
  }, true);
  async function api(action, payload) {
    const route = iecsAgent ? '/api/iecs/agent/' + action :
      '/api/smartport-agent/' + (action === 'state' ? 'state?native_input=true' : action);
    const response = await fetch(route, payload === undefined ? {cache:'no-store'} : {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
    });
    const result = await response.json();
    if(!response.ok) throw new Error(result.detail || 'Agent 暂不可用');
    return result;
  }
  const scene = iecsAgent ? document.querySelector('.container') : null;
  function layout() {
    const video = document.getElementById('remote-video');
    const surface = scene || video;
    if (!surface) return;
    const rect = surface.getBoundingClientRect(), scale = rect.width / (video?.videoWidth || 2560);
    const bottom = innerHeight - rect.bottom + 14 * scale;
    const rightGap = Math.max(10, 14 * scale);
    const width = Math.min(rect.width - 20, Math.max(300, Math.min(560, (video?.videoWidth || 2560) * .3) * scale));
    host.style.left = `${rect.x + rect.width / 2}px`;
    host.style.bottom = `${bottom}px`;
    host.style.width = `${width}px`;
    responseCard.style.right=`${Math.max(10, innerWidth - rect.right + 14 * scale)}px`;
    responseCard.style.bottom=`${bottom}px`;
    responseCard.style.maxHeight=`${Math.max(120,rect.height-140)}px`;
    if (iecsAgent) {
      const aboveInput = responseCard.offsetWidth + 8 > (rect.width - width) / 2 - rightGap;
      const offset = aboveInput ? host.offsetHeight + 8 : 0;
      responseCard.style.setProperty('--iecs-agent-response-bottom', `${bottom + offset}px`);
      responseCard.style.maxHeight = `${Math.max(64, rect.height - offset - 66)}px`;
    }
  }
  function scheduleDraft(){clearTimeout(draftTimer); draftTimer=setTimeout(() => {
    if(!iecsAgent && !composing && !posting && state && !state.busy) api('draft',{text:input.value}).catch(e => status.textContent=e.message);
  }, 250);}
  async function poll() {
    if(pollBusy) return; pollBusy = true;
    try {
      state = await api('state');
      if(!initialized){input.value = state.draft || ''; initialized = true;}
      host.hidden = false; renderResponse(); speech.update(state); layout();
      renderLiveCameras(state.live_camera);
      statistics?.update(state.sts_statistics ?? null);
      status.textContent = localError || (state.status === 'URL / Model saved (API key not saved)'
        ? '配置已保存，Key 仅保留在当前会话' : state.status);
      sendButton.disabled = state.busy || posting || state.configured === false;
    } catch(e){
      renderLiveCameras(null);
      statistics?.update(null);
      if(initialized || iecsAgent) {
        host.hidden = false; status.textContent = e.message;
        sendButton.disabled = true;
      }
    }
    finally{pollBusy=false;}
  }
  async function submit() {
    if(composing || posting || state?.busy || state?.configured === false || !input.value.trim()) return;
    clearTimeout(draftTimer); posting=true; sendButton.disabled=true;
    try {const result=await api('send',{text:input.value}); if(result.accepted){input.value=''; localError='';}}
    catch(e){localError=e.message; status.textContent=e.message;}
    finally{posting=false; await poll();}
  }
  host.querySelector('form').addEventListener('submit', e => {e.preventDefault(); submit();});
  host.querySelector('#web-agent-open').onclick = async () => {
    await poll(); if(!state) return;
    if (iecsAgent && !state.endpoint) {
      status.textContent = localError = '港区服务重载后可独立配置 Agent';
      return;
    }
    settings.elements.endpoint.value = state.endpoint; settings.elements.model.value = state.model;
    settings.elements.api_key.value = '';
    settings.elements.api_key.placeholder = state.key_configured ? '已配置；留空保留' : '请输入 API Key';
    settings.querySelector('[role=status]').textContent=''; modal.hidden=false; settings.elements.endpoint.focus();
  };
  modal.querySelector('#web-agent-close').onclick=()=>{modal.hidden=true; input.focus();};
  settings.onsubmit=async e=>{
    e.preventDefault(); const message=settings.querySelector('[role=status]');
    try {await api('settings',{endpoint:settings.elements.endpoint.value, model:settings.elements.model.value,api_key:settings.elements.api_key.value});
      settings.elements.api_key.value=''; localError=''; message.textContent='已保存'; await poll();
    } catch(error){message.textContent=error.message;}
  };
  window.addEventListener('resize',layout); document.getElementById('remote-video')?.addEventListener('resize',layout);
  if (scene) new ResizeObserver(layout).observe(scene);
  const speech={update(){},stop(){}}; // Voice UI temporarily disabled; text chat remains active.
  try{localStorage.removeItem("smartport-auto-speech");}catch{}
  setInterval(poll,700); poll();
}
