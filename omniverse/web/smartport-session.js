// A separate session control works for both administrator and spectator pages.
const response = await fetch('/auth/me', {cache:'no-store'});
if (response.status === 401) location.replace('/login');
else if (response.ok) {
  const session = await response.json();
  const panel = document.createElement('form');
  panel.method='post'; panel.action='/auth/logout';
  panel.style.cssText='position:fixed;left:14px;top:12px;z-index:10001;display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:6px;background:#142433df;color:#dce8f3;font:13px system-ui';
  const label=document.createElement('span');
  label.textContent=`${session.username} · ${session.role==='admin'?'管理员':'观众'}`;
  const button=document.createElement('button');button.type='submit';button.textContent='退出登录';
  button.style.cssText='padding:5px 9px;background:#344d62;color:white;border:0;border-radius:4px;cursor:pointer';
  panel.append(label,button);document.body.append(panel);
}

if(new URLSearchParams(location.search).get('spectator')==='1') {
  setInterval(()=>fetch('/auth/me',{cache:'no-store'}).then(r=>{if(r.status===401)location.replace('/login');}),30000);
  window.addEventListener('pagehide',()=>navigator.sendBeacon('/auth/release',''));
}
