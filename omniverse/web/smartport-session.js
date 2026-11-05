// A separate session control works for both administrator and spectator pages.
const response = await fetch('/auth/me', {cache:'no-store'});
if (response.status === 401) location.replace('/login');
else if (response.ok) {
  const session = await response.json();
  if(session.role==='admin'&&session.initial_view_pending){
    const home=await fetch('/api/session/initial-view',{method:'POST'});
    if(!home.ok)console.warn('RTG initial view is not ready; reload after Kit finishes startup.');
  }
  const panel = document.createElement('form');
  panel.method='post'; panel.action='/auth/logout';
  panel.style.cssText='position:fixed;left:14px;top:12px;z-index:10001;display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:6px;background:#142433df;color:#dce8f3;font:13px system-ui';
  const label=document.createElement('span');
  label.textContent=`${session.username} · ${{admin:'管理员',driver:'集卡司机',rtg_driver:'RTG司机',sts_driver:'STS司机',viewer:'观众'}[session.role]||session.role}`;
  const button=document.createElement('button');button.type='submit';button.textContent='退出登录';
  button.style.cssText='padding:5px 9px;background:#344d62;color:white;border:0;border-radius:4px;cursor:pointer';
  panel.append(label,button);
  if(session.role==='admin'){
    const link=document.createElement('a');link.href='/auth/driver-account';link.textContent='驾驶员账号';link.style.color='#9dd9ff';panel.append(link);
    const rtgLink=document.createElement('a');rtgLink.href='/auth/rtg-driver-account';rtgLink.textContent='RTG司机账号';rtgLink.style.color='#9dd9ff';panel.append(rtgLink);
    const stsLink=document.createElement('a');stsLink.href='/auth/sts-driver-account';stsLink.textContent='STS司机账号';stsLink.style.color='#9dd9ff';panel.append(stsLink);
  }
  document.body.append(panel);
}

if(new URLSearchParams(location.search).get('spectator')==='1') {
  setInterval(()=>fetch('/auth/me',{cache:'no-store'}).then(r=>{if(r.status===401)location.replace('/login');}),30000);
  window.addEventListener('pagehide',()=>navigator.sendBeacon('/auth/release',''));
}
