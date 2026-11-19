const API = '/api/smartport-agent';

function coordinates(position) {
  return [
    {label:'经度', value:`${position.longitude.toFixed(6)}°`},
    {label:'纬度', value:`${position.latitude.toFixed(6)}°`},
  ];
}

export function createSceneInspector() {
  const panel = document.createElement('aside');
  panel.id = 'web-container-info';
  panel.hidden = true;
  panel.setAttribute('aria-labelledby','web-container-title');
  panel.innerHTML = '<h2 id="web-container-title"></h2><dl></dl><small hidden></small>';
  const title = panel.querySelector('h2'), fields = panel.querySelector('dl'), basis = panel.querySelector('small');

  const toggle = document.createElement('button');
  toggle.id = 'web-device-map-toggle';
  toggle.type = 'button';
  toggle.textContent = '设备地图';
  toggle.setAttribute('aria-controls','web-device-map');
  toggle.setAttribute('aria-expanded','false');
  const map = document.createElement('aside');
  map.id = 'web-device-map';
  map.hidden = true;
  map.setAttribute('aria-labelledby','web-device-map-title');
  map.innerHTML = `
    <h2 id="web-device-map-title">港区设备地图</h2>
    <p class="map-datum">演示坐标</p>
    <div class="map-canvas" aria-label="港区俯视示意图，上北、下南、左西、右东">
      <span class="map-direction" data-side="top">N 北</span>
      <span class="map-direction" data-side="right">E 东</span>
      <span class="map-direction" data-side="bottom">S 南</span>
      <span class="map-direction" data-side="left">W 西</span>
    </div>
    <div class="map-legend"><span data-kind="sts">STS</span><span data-kind="rtg">RTG</span><span data-kind="truck">集卡</span></div>
    <select aria-label="选择地图设备"><option value="">选择设备</option></select>
    <output aria-live="polite"></output>`;
  document.body.append(panel,toggle,map);

  const canvas = map.querySelector('.map-canvas'), selection = map.querySelector('select'), status = map.querySelector('output');
  const dots = new Map();
  let state = null, activeId = null, picking = null, polling = false, timer = null, lastPanel = '';
  let pendingView = null, switching = false, viewError = '', mapError = '';

  function updateStatus() {
    status.textContent = viewError || mapError;
  }

  async function switchView() {
    if(switching || !pendingView) return;
    const id = pendingView;
    pendingView = null;
    switching = true;
    try {
      const response = await fetch(`${API}/device-view`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id}),
      });
      if(!response.ok) {
        const {detail} = await response.json();
        throw new Error(detail || '设备视角切换失败');
      }
    } catch(error) {
      if(activeId === id) viewError = error.message;
    } finally {
      switching = false;
      updateStatus();
      if(pendingView) switchView();
    }
  }

  function showPanel(name, rows, note = '') {
    const signature = JSON.stringify([name,rows,note]);
    if(lastPanel !== signature) {
      title.textContent = name;
      fields.replaceChildren(...rows.flatMap(({label,value}) => {
        const dt = document.createElement('dt'), dd = document.createElement('dd');
        dt.textContent = label;
        dd.textContent = value;
        return [dt,dd];
      }));
      basis.textContent = note;
      basis.hidden = !note;
      lastPanel = signature;
    }
    panel.hidden = false;
  }

  function showDevice(device) {
    const rows = coordinates(device.position);
    if(device.kind !== 'truck') {
      for(const [axis,label] of [['gantry','大车位置'],['trolley','小车位置'],['hoist','吊具位置']]) {
        const value = device.axes[axis];
        rows.push({label,value:Number.isFinite(value) ? `${value.toFixed(2)} m` : '—'});
      }
    }
    showPanel(device.name, rows, ['演示地图坐标',device.axis_basis].filter(Boolean).join(' · '));
  }

  function markSelection() {
    for(const [id,dot] of dots) dot.setAttribute('aria-pressed',String(id === activeId));
    selection.value = activeId ?? '';
  }

  function setMapOpen(open) {
    map.hidden = !open;
    toggle.setAttribute('aria-expanded',String(open));
    if(open) refresh();
  }

  function selectDevice(device, focusCamera = false) {
    if(!device) return;
    activeId = device.id;
    showDevice(device);
    markSelection();
    setMapOpen(true);
    if(focusCamera) {
      pendingView = device.id;
      switchView();
    }
  }

  function dismiss() {
    picking?.abort();
    picking = null;
    activeId = null;
    panel.hidden = true;
    viewError = '';
    updateStatus();
    markSelection();
  }

  function plot(position,bounds) {
    return {
      x:100*(position.longitude-bounds.sw.longitude)/(bounds.ne.longitude-bounds.sw.longitude),
      y:100*(bounds.ne.latitude-position.latitude)/(bounds.ne.latitude-bounds.sw.latitude),
    };
  }

  function render(snapshot) {
    const {map:layout,devices} = snapshot;
    if(!state) {
      map.querySelector('.map-datum').textContent = layout.name;
      for(const region of layout.regions) {
        const {x:left,y:bottom} = plot(region.sw,layout.bounds);
        const {x:right,y:top} = plot(region.ne,layout.bounds);
        const area = document.createElement('span');
        area.className = 'map-region';
        area.dataset.kind = region.kind;
        Object.assign(area.style,{left:`${left}%`,top:`${top}%`,width:`${right-left}%`,height:`${bottom-top}%`});
        area.textContent = region.name;
        canvas.insertBefore(area,canvas.querySelector('.map-direction'));
      }
      const center = plot(layout.control_center,layout.bounds);
      const landmark = document.createElement('span');
      landmark.className = 'map-landmark';
      landmark.style.left = `${center.x}%`;
      landmark.style.top = `${center.y}%`;
      const label = document.createElement('span');
      label.textContent = '主控台';
      landmark.append(label);
      canvas.append(landmark);
    }
    if(devices.map(d=>d.id).join() !== state?.devices.map(d=>d.id).join()) {
      const ids = new Set(devices.map(d=>d.id));
      for(const [id,dot] of dots) {
        if(ids.has(id)) continue;
        dot.remove();
        dots.delete(id);
      }
      selection.replaceChildren(new Option('选择设备',''),...devices.map(d=>new Option(d.name,d.id)));
    }
    state = snapshot;
    for(const device of devices) {
      let dot = dots.get(device.id);
      if(!dot) {
        dot = document.createElement('button');
        dot.type = 'button';
        dot.className = 'map-device';
        dot.dataset.kind = device.kind;
        dot.setAttribute('aria-label',device.name);
        dot.onclick = () => {
          dismiss();
          selectDevice(state.devices.find(d=>d.id===device.id),true);
        };
        canvas.append(dot);
        dots.set(device.id,dot);
      }
      const {x,y} = plot(device.position,layout.bounds);
      dot.hidden = x<0 || x>100 || y<0 || y>100;
      dot.title = `${device.name} · ${device.position.longitude.toFixed(6)}°, ${device.position.latitude.toFixed(6)}°`;
      Object.assign(dot.style,{left:`${x}%`,top:`${y}%`});
    }
    if(activeId) {
      const device = devices.find(d=>d.id===activeId);
      if(device) showDevice(device);
      else dismiss();
    }
    markSelection();
  }

  async function refresh() {
    if(polling || document.hidden || (map.hidden && !activeId)) return;
    clearTimeout(timer);
    polling = true;
    try {
      const response = await fetch(`${API}/device-map`,{cache:'no-store'});
      if(!response.ok) throw new Error('设备位置暂不可用');
      render(await response.json());
      mapError = '';
    } catch(error) {
      mapError = error.message;
    } finally {
      updateStatus();
      polling = false;
      if(!map.hidden || activeId) timer = setTimeout(refresh,1000);
    }
  }

  async function pick(point) {
    dismiss();
    const request = picking = new AbortController();
    try {
      const response = await fetch(`${API}/inspect-at`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(point),signal:request.signal,
      });
      if(!response.ok) throw new Error(`场景点选失败（${response.status}）`);
      const {rows,device} = await response.json();
      if(picking !== request) return;
      if(device) selectDevice(device);
      else if(rows.length) showPanel('箱信息',rows);
    } catch(error) {
      if(!request.signal.aborted) console.warn(error);
    } finally {
      if(picking === request) picking = null;
    }
  }

  toggle.onclick = () => setMapOpen(map.hidden);
  selection.onchange = () => {
    const device = state?.devices.find(d=>d.id===selection.value);
    dismiss();
    selectDevice(device,true);
  };
  document.addEventListener('visibilitychange',refresh);
  return {pick,dismiss};
}
