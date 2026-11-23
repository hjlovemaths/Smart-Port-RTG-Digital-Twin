if (document.documentElement.classList.contains('smartport-iecs')) {
  const speedOptions = [1,5,10,20].map(value => `<option value="${value}">${value}×</option>`).join('');
  const host = document.createElement('aside');
  host.id = 'iecs-workbench';
  host.setAttribute('aria-labelledby', 'iecs-title');
  host.innerHTML = `
    <h1 id="iecs-title">IECS 作业工作台</h1>
    <nav class="iecs-toolbar iecs-modes" aria-label="调度模式">
      <button data-mode="simulation" aria-pressed="true">模拟调度</button>
      <button data-mode="external" aria-pressed="false">真实 IECS 调度</button>
    </nav>
    <p class="iecs-status" data-state>正在读取仿真状态</p>
    <p class="iecs-feedback" data-feedback role="status"></p>
    <section data-simulation>
      <h2>派单与聚焦</h2>
      <label for="iecs-flow">标准流程</label>
      <select id="iecs-flow">
        <option value="unload">卸船入场</option><option value="load">堆场装船</option>
        <option value="gate_in">外车进场落箱</option><option value="gate_out">外车提箱出场</option>
        <option value="transfer">场内转栈</option>
      </select>
      <div class="iecs-toolbar">
        <button class="iecs-primary" data-action="start">启动仿真</button>
        <button class="iecs-primary" data-action="submit">提交一单</button>
        <button data-action="mixed">混合作业（5 单并行）</button>
        <button data-action="focus">聚焦当前任务</button>
      </div>
    </section>
    <section data-simulation>
      <h2>演示控制</h2>
      <div class="iecs-toolbar">
        <button data-action="pause">暂停</button><button data-action="resume">继续</button>
        <button data-action="check_scene">检查场景</button><button data-action="reset">复位演示</button>
      </div>
      <label for="iecs-speed">仿真速度</label>
      <select id="iecs-speed">${speedOptions}</select>
    </section>
    <section data-external hidden>
      <h2>IECS 接入</h2>
      <p>一车一箱卸船入场 · 数字桥吊与 RTG</p>
      <div class="iecs-toolbar">
        <button class="iecs-primary" data-link="start">开启接入</button>
        <button data-link="focus">聚焦作业</button>
        <button data-link="stop">关闭接入</button>
      </div>
      <label for="iecs-external-speed">数字场景速度</label>
      <select id="iecs-external-speed">${speedOptions}</select>
      <dl class="iecs-link-details" data-link-details></dl>
    </section>
    <section data-task-section><h2>任务与载箱</h2><ol data-orders></ol><p data-checks></p></section>
    <section data-traffic-section hidden>
      <details id="iecs-traffic">
        <summary>IECS 收发报文</summary>
        <div class="iecs-toolbar">
          <label for="iecs-message-direction">方向</label>
          <select id="iecs-message-direction"><option value="">全部</option><option value="received">收到</option><option value="sent">发出</option></select>
          <label><input type="checkbox" data-traffic-heartbeat> 包含心跳</label>
        </div>
        <label for="iecs-message">本轮报文</label>
        <select id="iecs-message"><option value="">跟随最新</option></select>
        <p data-traffic-status>等待报文</p>
        <div data-traffic-payload hidden>
          <h3 data-request-label>请求</h3><pre data-request></pre>
          <h3 data-response-label>响应</h3><pre data-response></pre>
        </div>
      </details>
    </section>
    <section data-event-section>
      <h2>事件记录</h2><ol class="iecs-event" data-events></ol>
      <div class="iecs-toolbar">
        <button data-history="older">更早事件</button><button data-history="latest">返回最新</button>
      </div>
    </section>`;
  document.body.append(host);

  const flow = host.querySelector('#iecs-flow');
  const speed = host.querySelector('#iecs-speed');
  const externalSpeed = host.querySelector('#iecs-external-speed');
  const trafficPanel = host.querySelector('#iecs-traffic');
  const direction = host.querySelector('#iecs-message-direction');
  const heartbeat = host.querySelector('[data-traffic-heartbeat]');
  const messageSelect = host.querySelector('#iecs-message');
  const trafficStatus = host.querySelector('[data-traffic-status]');
  const trafficPayload = host.querySelector('[data-traffic-payload]');
  const feedback = host.querySelector('[data-feedback]');
  const orderList = host.querySelector('[data-orders]');
  const names = Object.fromEntries(Array.from(flow.options, option => [option.value, option.textContent]));
  const statusNames = {queued:'排队中',running:'执行中',completed:'已完成'};
  const eventNames = {order_created:'订单提交',pause:'暂停',resume:'继续',reset:'复位',speed:'调整倍率',
    command_rejected:'指令被拒绝',order_completed:'订单完成',ship_demo_box_bound:'绑定演示船箱',gate_admitted:'外车入闸'};
  let state = {running:false};
  let mode = null;
  let pending = false;
  let refreshInFlight = false;
  let commandGeneration = 0;
  let historyCursor = null;
  let browsingHistory = false;
  let trafficRevision = 0;
  let trafficInFlight = false;
  let trafficList = '';
  let trafficEntry = '';
  async function api(path, payload) {
    const response = await fetch('/api/iecs/' + path, payload ? {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    } : {cache:'no-store'});
    if (response.status === 401) location.replace('/login');
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'IECS 请求失败');
    return data;
  }
  function list(selector, items, empty) {
    const target = host.querySelector(selector);
    const nodes = (items.length ? items : [empty]).map(text => {
      const li = document.createElement('li'); li.textContent = text; return li;
    });
    target.replaceChildren(...nodes);
  }
  function eventLine(event) {
    const details = event.payload || event;
    if (event.kind === 'iecs_message') return `${event.time.toFixed(1)} 秒 · ${details.direction} ${details.pid ?? ''} · ${details.message}`;
    if (event.kind === 'iecs_fault') return `${event.time.toFixed(1)} 秒 · 联调暂停 · ${details.error}`;
    const timer = event.kind === 'order_completed' && Number.isFinite(details.elapsed_s)
      ? ` · 耗时 ${details.elapsed_s.toFixed(1)} 仿真秒（等待 ${details.wait_s.toFixed(1)} 秒）` : '';
    return `${event.time.toFixed(1)} 秒 · ${eventNames[event.kind] || event.kind} ${details.order || ''}${timer}`;
  }
  function renderOrders(orders, allowFocus) {
    if (!orders.length) {
      list('[data-orders]', [], '暂无订单');
      return;
    }
    const rows = new Map(Array.from(orderList.children, row => [row.dataset.order, row]));
    orders.forEach((order, index) => {
      let row = rows.get(order.id);
      if (!row) {
        row = document.createElement('li');
        row.dataset.order = order.id;
        row.innerHTML = '<div class="iecs-order-row"><span></span><button type="button">聚焦</button></div>';
      }
      const button = row.querySelector('button');
      button.dataset.orderFocus = order.id;
      button.setAttribute('aria-label', `聚焦第 ${index + 1} 条任务 ${order.id}`);
      button.hidden = !allowFocus;
      button.disabled = pending;
      const vehicle = (state.vehicles || []).find(item => item.id === order.vehicle);
      const truckLabel = vehicle?.id.startsWith('EXT-') && vehicle.number !== vehicle.id
        ? `${vehicle.id}（${vehicle.number}）` : order.vehicle || '待派车';
      let location = '';
      if (order.flow === 'gate_out' && vehicle) {
        if (vehicle.status === 'waiting_gate') location = ' · 入口候场';
        else if (vehicle.status === 'departed') location = ' · 已出场';
        else if (order.step === 1) location = ` · 取箱位 ${order.source.block}/${String(order.source.bay).padStart(3, '0')}`;
        else if (order.step >= 3) location = ' · 出口车道';
      }
      const waitingFor = order.wait_reason?.replace('Collision wait:', '等待前方车辆');
      const summary = waitingFor || (Number.isFinite(order.elapsed_s) ? `耗时 ${order.elapsed_s.toFixed(1)} 仿真秒` : order.step_name || '');
      const text = `${order.id} · ${names[order.flow]} · ${statusNames[order.status] || order.status} · ${truckLabel}${location} · ${summary}${vehicle?.load ? ' · 载箱 ' + vehicle.load : ''}`;
      if (row.querySelector('span').textContent !== text) row.querySelector('span').textContent = text;
      if (orderList.children[index] !== row) orderList.insertBefore(row, orderList.children[index] || null);
      rows.delete(order.id);
    });
    for (const row of rows.values()) row.remove();
  }
  function render() {
    const externalTab = mode === 'external';
    const external = state.external;
    const matchesMode = externalTab === Boolean(external);
    const activeOrders = state.orders || [];
    const orders = matchesMode ? activeOrders : [];
    const working = orders.some(order => order.status !== 'completed');
    const completed = orders.filter(order => order.status === 'completed').length;
    const active = orders.filter(order => order.status === 'running').length;
    const waiting = orders.filter(order => order.status !== 'completed' && order.wait_reason).length;
    for (const button of host.querySelectorAll('[data-mode]')) {
      button.setAttribute('aria-pressed', String(button.dataset.mode === (mode || 'simulation')));
    }
    for (const section of host.querySelectorAll('[data-simulation]')) section.hidden = externalTab;
    host.querySelector('[data-external]').hidden = !externalTab;
    host.querySelector('[data-task-section]').hidden = !matchesMode;
    host.querySelector('[data-traffic-section]').hidden = !externalTab || !external;
    host.querySelector('[data-event-section]').hidden = !matchesMode;
    host.querySelector('[data-state]').textContent = externalTab
      ? (external ? `${external.fault || external.phase} · ${state.speed}×` : '真实 IECS 接入尚未开启')
      : external ? '真实 IECS 正在接入，请在其页面关闭接入后启动模拟。'
      : state.running
      ? `${state.paused ? '已暂停' : '运行中'} · ${state.speed}× · ${Math.floor(state.simulation_time)} 秒 · 车辆 ${active}/${state.max_active_trucks || 5} · 等待 ${waiting} 单 · 完成 ${completed}/${orders.length} 单`
      : '仿真尚未启动';
    const details = external ? [
      ['设备入口', external.listen + '/process'], ['IECS 回调', external.peer],
      ['设备', `${external.qc} / ${external.rtg}`],
      ['集卡', `${external.vehicle} · 场景 ${external.truck_number} 号`],
      ['回调状态', external.callback_error ? '暂未连通 · ' + external.callback_error :
        external.last_callback ? '已收到 IECS 响应' : '正在等待 IECS 响应'],
      ['最近来报', external.last_seen ? new Date(external.last_seen * 1000).toLocaleTimeString() : '等待 IECS 消息'],
      ['设备任务', [external.qc_task, external.rtg_task].filter(Boolean).join(' / ')],
      ['箱号及归属', external.box ? `${external.box} · ${external.location}` : ''],
      ['源位 → 目标位', external.ship_slot ? `${external.ship_slot} → ${external.yard_slot || '待 IECS 指定'}` : ''],
      ['可用联调示例', external.sample.ship_slot + ' → ' + (external.sample.yard_slot || '暂无空位')],
    ] : [];
    host.querySelector('[data-link-details]').replaceChildren(...details.filter(([,value]) => value).flatMap(([label,value]) => {
      const term = document.createElement('dt'); term.textContent = label;
      const description = document.createElement('dd'); description.textContent = value;
      return [term, description];
    }));
    host.querySelector('[data-link="start"]').disabled = pending || !!external || activeOrders.some(order => order.status !== 'completed');
    host.querySelector('[data-link="focus"]').disabled = pending || !external || !orders.some(order => order.vehicle && order.status === 'running');
    const stopLink = host.querySelector('[data-link="stop"]');
    stopLink.disabled = pending || !external || (working && !external.fault);
    stopLink.textContent = external?.fault && working ? '结束本轮并恢复场景' : '关闭接入';
    host.querySelector('[data-checks]').textContent = state.scene_checks
      ? (state.scene_checks.ok ? '场景检查通过' : state.scene_checks.errors.join(' / ')) : '';
    renderOrders(orders, !externalTab);
    if (!browsingHistory) {
      const events = (state.events || []).slice(-8).reverse();
      list('[data-events]', events.map(eventLine), '暂无事件');
    }
    for (const button of host.querySelectorAll('button[data-action]')) {
      const action = button.dataset.action;
      button.disabled = pending || !!external || (action === 'start' ? state.running :
        !state.running ||
        (action === 'mixed' && orders.length > 0) || (action === 'focus' && !working) ||
        (action === 'pause' && state.paused) || (action === 'resume' && !state.paused));
    }
    flow.disabled = pending;
    speed.disabled = pending || !!external || !state.running;
    externalSpeed.disabled = pending || !external;
    if (external) externalSpeed.value = String(state.speed);
    if (state.running && [...speed.options].some(option => +option.value === state.speed)) speed.value = String(state.speed);
    host.querySelector('[data-history="older"]').disabled = pending || !state.running || !matchesMode;
  }
  async function refresh() {
    if (pending || refreshInFlight) return;
    refreshInFlight = true;
    const generation = commandGeneration;
    try {
      const next = await api('state');
      if (!pending && generation === commandGeneration) {
        if (Boolean(state.external) !== Boolean(next.external)) {
          browsingHistory = false; historyCursor = null; resetTraffic();
        }
        state = next;
        if (mode === null) mode = next.external ? 'external' : 'simulation';
        render();
      }
    } catch (error) { feedback.textContent = error.message; }
    finally { refreshInFlight = false; }
  }
  function resetTraffic() {
    trafficRevision++;
    trafficList = '';
    trafficEntry = '';
    messageSelect.replaceChildren(new Option('跟随最新',''));
    trafficPayload.hidden = true;
    trafficStatus.textContent = '等待报文';
  }
  function messageLine(message) {
    const timestamp = new Date(message.time * 1000).toLocaleTimeString('zh-CN',{hour12:false});
    const side = message.direction === 'received' ? '收到' : '发出';
    const status = message.pending ? '处理中' : message.error ? '失败' : `HTTP ${message.status}`;
    return [timestamp,side,message.pid == null ? message.path : `PID ${message.pid}`,status].join(' · ');
  }
  async function refreshTraffic() {
    if (trafficInFlight || !trafficPanel.open || mode !== 'external' || !state.external) return;
    trafficInFlight = true;
    const revision = trafficRevision;
    try {
      const query = new URLSearchParams({direction:direction.value,heartbeat:String(heartbeat.checked)});
      const {messages} = await api('external/messages?' + query);
      if (revision !== trafficRevision) return;
      const listKey = JSON.stringify(messages);
      if (listKey !== trafficList) {
        const selected = messageSelect.value;
        messageSelect.replaceChildren(new Option('跟随最新',''), ...messages.map(msg => new Option(messageLine(msg),msg.id)));
        messageSelect.value = messages.some(msg => msg.id === selected) ? selected : '';
        trafficList = listKey;
      }
      const next = messages.find(msg => msg.id === messageSelect.value) || messages[0];
      if (!next) {
        trafficEntry = ''; trafficStatus.textContent = '暂无符合条件的报文'; trafficPayload.hidden = true;
        return;
      }
      const entryKey = JSON.stringify(next);
      if (entryKey === trafficEntry) return;
      const {message} = await api('external/messages?message_id=' + next.id);
      if (revision !== trafficRevision || !message) return;
      trafficEntry = entryKey;
      trafficStatus.textContent = [messageLine(message),message.path,message.machine,
        message.attempts > 1 ? `尝试 ${message.attempts} 次` : '',message.error].filter(Boolean).join(' · ');
      const received = message.direction === 'received';
      host.querySelector('[data-request-label]').textContent = received ? 'IECS 发来的请求' : 'Twin 发出的请求';
      host.querySelector('[data-response-label]').textContent = received ? 'Twin 返回的响应' : 'IECS 返回的响应';
      host.querySelector('[data-request]').textContent = message.request || '无报文正文';
      host.querySelector('[data-response]').textContent = message.pending ? '等待响应' : message.response || '无报文正文';
      trafficPayload.hidden = false;
    } catch (error) {
      if (revision === trafficRevision) trafficStatus.textContent = error.message;
    } finally {
      trafficInFlight = false;
      if (revision !== trafficRevision) refreshTraffic();
    }
  }
  async function command(action, extra = {}) {
    if (pending) return;
    if (action === 'reset' && !window.confirm('复位将清空本轮订单并恢复场景，继续吗？')) return;
    commandGeneration++; pending = true; feedback.textContent = ''; render();
    try {
      state = await api('command', {action, ...extra});
      feedback.textContent = state.focus
        ? `已聚焦 ${state.focus.order_id} · ${state.focus.focus_label}` : state.error || '';
      if (action === 'reset') { browsingHistory = false; historyCursor = null; }
    } catch (error) { feedback.textContent = error.message; }
    finally { pending = false; render(); }
  }
  async function externalCommand(action, extra = {}) {
    if (pending) return;
    commandGeneration++; pending = true; feedback.textContent = ''; render();
    try {
      state = await api('external/command', {action, ...extra});
      if (action === 'start' || action === 'stop') { browsingHistory = false; historyCursor = null; resetTraffic(); }
    } catch (error) { feedback.textContent = error.message; }
    finally { pending = false; render(); }
  }
  host.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button || pending) return;
    if (button.dataset.mode) {
      mode = button.dataset.mode; browsingHistory = false; historyCursor = null;
      feedback.textContent = ''; render();
      refreshTraffic();
    } else if (button.dataset.link) {
      const action = button.dataset.link;
      const abort = action === 'stop' && Boolean(state.external?.fault && state.orders?.some(order => order.status !== 'completed'));
      if (abort && !window.confirm('结束本轮联调会恢复数字场景，IECS 上游任务需由调度员核对。继续吗？')) return;
      if ((action === 'start' || action === 'stop') && !abort && state.orders?.length &&
          !window.confirm('此操作会恢复本轮数字场景，继续吗？')) return;
      await externalCommand(action, {abort});
    } else if (button.dataset.orderFocus) {
      await command('focus', {order_id:button.dataset.orderFocus});
    } else if (button.dataset.action) {
      const action = button.dataset.action;
      await command(action, action === 'submit' ? {flow:flow.value} : {});
    } else if (button.dataset.history === 'latest') {
      browsingHistory = false; historyCursor = null; render();
    } else if (button.dataset.history === 'older') {
      pending = true; render();
      try {
        const page = await api('events' + (historyCursor === null ? '' : '?before=' + historyCursor));
        if (page.events.length) {
          browsingHistory = true; historyCursor = page.events.at(-1).id;
          list('[data-events]', page.events.map(e => `#${e.id} · ${eventLine(e)}`), '');
          feedback.textContent = '';
        } else feedback.textContent = '没有更早的事件';
      } catch (error) { feedback.textContent = error.message; }
      finally { pending = false; render(); }
    }
  });
  speed.addEventListener('change', () => command('speed', {speed:Number(speed.value)}));
  externalSpeed.addEventListener('change', () => externalCommand('speed', {speed:Number(externalSpeed.value)}));
  trafficPanel.addEventListener('toggle', refreshTraffic);
  for (const control of [direction,heartbeat]) {
    control.addEventListener('change', () => { resetTraffic(); refreshTraffic(); });
  }
  messageSelect.addEventListener('change', () => { trafficRevision++; refreshTraffic(); });
  render();
  await refresh();
  setInterval(refresh, 900);
  setInterval(refreshTraffic, 1000);
}
