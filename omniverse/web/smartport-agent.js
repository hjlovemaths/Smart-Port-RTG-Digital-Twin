// Browser-native IME controls. Scene operations still use the existing Agent.
if (document.documentElement.classList.contains('smartport-video-only') && !document.documentElement.classList.contains('smartport-spectator')) {
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
  document.body.append(host, modal, responseCard);
  responseCard.querySelector('#web-agent-clear').onclick=()=>api('clear',{}).then(poll).catch(e=>status.textContent=e.message);
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
    if(state.approval)approval.querySelector('p').textContent=`确认场景操作：${state.approval.name} ${JSON.stringify(state.approval.arguments)}`;
  }
  const input = host.querySelector('input'), sendButton = host.querySelector('#web-agent-send');
  const status = host.querySelector('[role=status]'), settings = modal.querySelector('form');
  let composing = false, state = null, pollBusy = false, posting = false, initialized = false, draftTimer, localError = '';
  // Installed before the SDK's document listeners; keep native IME/default
  // browser editing, but do not also send these keystrokes to the 3D viewport.
  for (const name of ['keydown','keyup','keypress','beforeinput','input','compositionstart','compositionupdate','compositionend']) {
    document.addEventListener(name, event => {
      if (host.contains(event.target) || modal.contains(event.target) || responseCard.contains(event.target)) event.stopImmediatePropagation();
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
    const route = action === 'state' ? 'state?native_input=true' : action;
    const response = await fetch('/api/smartport-agent/' + route, payload === undefined ? {cache:'no-store'} : {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
    });
    const result = await response.json();
    if(!response.ok) throw new Error(result.detail || 'Agent 暂不可用');
    return result;
  }
  function layout() {
    const video = document.getElementById('remote-video'); if(!video) return;
    const rect = video.getBoundingClientRect(), scale = rect.width / (video.videoWidth || 2560);
    host.style.left = `${rect.x + rect.width / 2}px`;
    host.style.bottom = `${innerHeight - rect.bottom + 14 * scale}px`;
    responseCard.style.right=`${Math.max(10,innerWidth-rect.right+14*scale)}px`;
    responseCard.style.bottom=`${innerHeight-rect.bottom+14*scale}px`;
    responseCard.style.maxHeight=`${Math.max(120,rect.height-140)}px`;
    host.style.width = `${Math.min(rect.width - 20, Math.max(300, Math.min(560, (video.videoWidth || 2560) * .3) * scale))}px`;
  }
  function scheduleDraft(){clearTimeout(draftTimer); draftTimer=setTimeout(() => {
    if(!composing && !posting && state && !state.busy) api('draft',{text:input.value}).catch(e => status.textContent=e.message);
  }, 250);}
  async function poll() {
    if(pollBusy) return; pollBusy = true;
    try {
      state = await api('state');
      if(!initialized){input.value = state.draft || ''; initialized = true;}
      host.hidden = false; renderResponse(); layout();
      status.textContent = localError || (state.status === 'URL / Model saved (API key not saved)'
        ? '配置已保存，Key 仅保留在当前会话' : state.status);
      sendButton.disabled = state.busy || posting;
    } catch(e){if(initialized) status.textContent = e.message;}
    finally{pollBusy=false;}
  }
  async function submit() {
    if(composing || posting || state?.busy || !input.value.trim()) return;
    clearTimeout(draftTimer); posting=true; sendButton.disabled=true;
    try {const result=await api('send',{text:input.value}); if(result.accepted){input.value=''; localError='';}}
    catch(e){localError=e.message; status.textContent=e.message;}
    finally{posting=false; await poll();}
  }
  host.querySelector('form').addEventListener('submit', e => {e.preventDefault(); submit();});
  host.querySelector('#web-agent-open').onclick = async () => {
    await poll(); if(!state) return;
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
  setInterval(poll,700); poll();
}
