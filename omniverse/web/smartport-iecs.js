if (document.documentElement.classList.contains('smartport-iecs')) {
  const host = document.createElement('aside');
  host.id = 'iecs-workbench';
  host.setAttribute('aria-labelledby', 'iecs-title');
  host.innerHTML = `
    <h1 id="iecs-title">IECS 作业工作台</h1>
    <p class="iecs-status" data-state>正在读取仿真状态</p>
    <p class="iecs-feedback" data-feedback role="status"></p>
    <section>
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
        <button data-action="mixed">混合作业</button>
        <button data-action="focus">聚焦任务</button>
      </div>
    </section>
    <section>
      <h2>演示控制</h2>
      <div class="iecs-toolbar">
        <button data-action="pause">暂停</button><button data-action="resume">继续</button>
        <button data-action="check_scene">检查场景</button><button data-action="reset">复位演示</button>
      </div>
      <label for="iecs-speed">仿真速度</label>
      <select id="iecs-speed"><option value="1">1×</option><option value="5">5×</option><option value="10">10×</option><option value="20">20×</option></select>
    </section>
    <section><h2>任务与载箱</h2><ol data-orders></ol><p data-checks></p></section>
    <section>
      <h2>事件记录</h2><ol class="iecs-event" data-events></ol>
      <div class="iecs-toolbar">
        <button data-history="older">更早事件</button><button data-history="latest">返回最新</button>
      </div>
    </section>`;
  document.body.append(host);

  const flow = host.querySelector('#iecs-flow');
  const speed = host.querySelector('#iecs-speed');
  const feedback = host.querySelector('[data-feedback]');
  const names = Object.fromEntries(Array.from(flow.options, option => [option.value, option.textContent]));
  const statusNames = {queued:'排队中',running:'执行中',completed:'已完成'};
  const eventNames = {order_created:'订单提交',pause:'暂停',resume:'继续',reset:'复位',speed:'调整倍率',
    command_rejected:'指令被拒绝',order_completed:'订单完成',ship_demo_box_bound:'绑定演示船箱'};
  let state = {running:false};
  let pending = false;
  let refreshInFlight = false;
  let historyCursor = null;
  let browsingHistory = false;
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
  function render() {
    const orders = state.orders || [];
    const working = orders.some(order => order.status !== 'completed');
    const completed = orders.filter(order => order.status === 'completed').length;
    host.querySelector('[data-state]').textContent = state.running
      ? `${state.paused ? '已暂停' : '运行中'} · ${state.speed}× · ${Math.floor(state.simulation_time)} 秒 · 完成 ${completed}/${orders.length} 单 · ${state.size_ft} 尺演示`
      : '仿真尚未启动';
    host.querySelector('[data-checks]').textContent = state.scene_checks
      ? (state.scene_checks.ok ? '场景检查通过' : state.scene_checks.errors.join(' / ')) : '';
    list('[data-orders]', orders.map(order => {
      const vehicle = (state.vehicles || []).find(item => item.id === order.vehicle);
      return `${order.id} · ${names[order.flow]} · ${statusNames[order.status] || order.status} · ${order.vehicle || '待派车'} · ${order.step_name || ''}${vehicle?.load ? ' · 载箱 ' + vehicle.load : ''}`;
    }), '暂无订单');
    if (!browsingHistory) {
      const events = (state.events || []).slice(-8).reverse();
      list('[data-events]', events.map(e => `${e.time.toFixed(1)} 秒 · ${eventNames[e.kind] || e.kind} ${e.order || ''}`), '暂无事件');
    }
    for (const button of host.querySelectorAll('button[data-action]')) {
      const action = button.dataset.action;
      button.disabled = pending || (action === 'start' ? state.running :
        !state.running || ((action === 'submit' || action === 'mixed') && working) ||
        (action === 'mixed' && orders.length > 0) || (action === 'focus' && !working) ||
        (action === 'pause' && state.paused) || (action === 'resume' && !state.paused));
    }
    flow.disabled = pending || working;
    speed.disabled = pending || !state.running;
    if (state.running && [...speed.options].some(option => +option.value === state.speed)) speed.value = String(state.speed);
    host.querySelector('[data-history="older"]').disabled = pending || !state.running;
  }
  async function refresh() {
    if (pending || refreshInFlight) return;
    refreshInFlight = true;
    try {
      const next = await api('state');
      if (!pending) { state = next; render(); }
    } catch (error) { feedback.textContent = error.message; }
    finally { refreshInFlight = false; }
  }
  async function command(action, extra = {}) {
    if (pending) return;
    if (action === 'reset' && !window.confirm('复位将清空本轮订单并恢复场景，继续吗？')) return;
    pending = true; feedback.textContent = ''; render();
    try {
      state = await api('command', {action, ...extra});
      feedback.textContent = state.error || '';
      if (action === 'reset') { browsingHistory = false; historyCursor = null; }
    } catch (error) { feedback.textContent = error.message; }
    finally { pending = false; render(); }
  }
  host.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button || pending) return;
    if (button.dataset.action) {
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
          list('[data-events]', page.events.map(e => `#${e.id} · ${e.time.toFixed(1)} 秒 · ${eventNames[e.kind] || e.kind} ${e.payload.order || ''}`), '');
          feedback.textContent = '';
        } else feedback.textContent = '没有更早的事件';
      } catch (error) { feedback.textContent = error.message; }
      finally { pending = false; render(); }
    }
  });
  speed.addEventListener('change', () => command('speed', {speed:Number(speed.value)}));
  render();
  await refresh();
  setInterval(refresh, 900);
}
