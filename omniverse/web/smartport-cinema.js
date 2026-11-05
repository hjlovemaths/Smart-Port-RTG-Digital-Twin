// A small shared phase cue; viewers can read it, only administrators can control.
if(document.documentElement.classList.contains('smartport-video-only') && !document.documentElement.classList.contains('smartport-driver')) {
  const cue=document.createElement('aside');cue.id='transfer-cinema';cue.hidden=true;
  cue.innerHTML='<strong></strong><span class="route"></span><span class="detail"></span><progress max="10" value="0" aria-label="搬运流程进度"></progress><div class="controls" hidden><button type="button" data-action="pause">暂停镜头</button><button type="button" data-action="stop">退出镜头</button></div><small role="status"></small>';
  document.body.append(cue);
  let admin=false,busy=false,current=null,stopped=false;
  fetch('/auth/me',{cache:'no-store'}).then(r=>r.ok?r.json():{}).then(r=>admin=r.role==='admin').catch(()=>{});
  function layout(){
    const video=document.getElementById('remote-video');if(!video)return;
    const rect=video.getBoundingClientRect();cue.style.left=`${rect.left+16}px`;cue.style.top=`${rect.top+16}px`;
  }
  async function poll(){
    if(busy||stopped)return;busy=true;
    try{
      const response=await fetch('/api/transfer-camera/state',{cache:'no-store'});
      if(!response.ok){cue.hidden=true;return;}
      current=await response.json();cue.hidden=!current.visible;if(cue.hidden)return;
      const paused=current.status==='paused';
      cue.querySelector('strong').textContent=`电影视角 · ${current.title}${paused?'（已暂停）':current.status==='task_paused'?'（任务暂停）':''}`;
      cue.querySelector('.route').textContent=`${current.direction==='yard_to_ship'?'':'船上 '}${current.source} → ${current.destination}`;
      cue.querySelector('.detail').textContent=paused?(current.task_finished?'搬运已完成，保留手动视角':'手动查看中，可继续自动镜头'):current.detail;
      cue.querySelector('progress').value=current.step;cue.querySelector('progress').max=current.total;
      cue.querySelector('.controls').hidden=!admin;
      const button=cue.querySelector('[data-action]:not([data-action="stop"])');
      button.dataset.action=paused?'resume':'pause';button.textContent=paused?'继续自动镜头':'暂停镜头';
      layout();
    }catch{cue.hidden=true;}finally{busy=false;}
  }
  cue.addEventListener('click',async event=>{
    const button=event.target.closest('[data-action]');if(!button||!admin)return;
    event.stopPropagation();button.disabled=true;
    try{
      const response=await fetch('/api/smartport-agent/cinema',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:button.dataset.action})});
      const result=await response.json();if(!response.ok)throw new Error(result.detail||'镜头操作暂不可用');
      cue.querySelector('small').textContent='';await poll();
    }catch(error){cue.querySelector('small').textContent=error.message;}finally{button.disabled=false;}
  });
  for(const name of ['keydown','keyup','pointerdown','mousedown','mouseup','wheel'])
    window.addEventListener(name,event=>{if(cue.contains(event.target))event.stopImmediatePropagation();},true);
  window.addEventListener('resize',layout);document.getElementById('remote-video')?.addEventListener('resize',layout);
  const timer=setInterval(poll,600);window.addEventListener('pagehide',()=>{stopped=true;clearInterval(timer);});poll();
}
