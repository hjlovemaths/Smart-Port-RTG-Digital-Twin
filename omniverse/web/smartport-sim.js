import {pointcloudPreview} from './smartport-pointcloud-preview.js?v=2';

if (location.pathname === '/sim') {
  const axes = {gantry_m: '大车', trolley_m: '小车', hoist_m: '吊具'};
  const devices = {rtg: '主 RTG', sts01: 'STS_01'};
  const labels = {running: '运行中', completed: '动作结束', cancelled: '已停止', failed: '运行失败'};
  const metres = value => Number.isFinite(value) ? `${value.toFixed(3)} m` : '—';
  const panel = document.createElement('aside');
  panel.id = 'sim-workbench';
  panel.setAttribute('aria-labelledby', 'sim-title');
  panel.innerHTML = `
    <h1 id="sim-title">SIM 调试工作台</h1>
    <div class="sim-toolbar sim-devices" role="group" aria-label="调试设备">
      <button type="button" data-equipment="rtg" aria-pressed="true">主 RTG</button>
      <button type="button" data-equipment="sts01" aria-pressed="false">STS_01</button>
    </div>
    <p class="sim-toolbar">
      <button type="button" data-action="focus">聚焦主 RTG</button>
      <button type="button" data-action="stop" disabled>停止调试动作</button>
    </p>
    <p class="sim-notice" role="status"></p>
    <section aria-labelledby="sim-pointcloud-title">
      <h2 id="sim-pointcloud-title">点云采集</h2>
      <div class="sim-toolbar sim-capture-controls">
        <label>量程
          <select id="sim-scan-range">
            <option value="20">20 m</option>
            <option value="40" selected>40 m</option>
            <option value="60">60 m</option>
          </select>
        </label>
        <button type="button" data-action="capture_pointcloud" disabled>采集点云</button>
        <button type="button" data-action="scan_view" disabled>查看箱区</button>
      </div>
      <p class="sim-detail sim-mount-summary"></p>
      <details>
        <summary>安装参数</summary>
        <ul class="sim-mounts"></ul>
      </details>
      <p class="sim-scan-status" role="status">等待采集</p>
      <p class="sim-detail sim-scan-result" hidden></p>
      <figure class="sim-pointcloud-preview" hidden>
        <div class="sim-preview-status" role="status"></div>
        <canvas aria-label="采集点云" hidden></canvas>
        <figcaption class="sim-preview-footer">
          <span class="sim-preview-legend"><span>周边</span><span>箱体</span><span>RTG</span></span>
          <button type="button" class="sim-preview-reset" disabled>重置</button>
        </figcaption>
        <p class="sim-detail sim-preview-help">拖动旋转 · 滚轮缩放 · Shift + 拖动平移</p>
      </figure>
      <div class="sim-toolbar sim-downloads" hidden></div>
    </section>
    ${Object.entries(axes).map(([key, name]) => `
      <section data-axis="${key}">
        <div class="sim-axis-header"><h2>${name}</h2><output class="sim-position">—</output></div>
        <p class="sim-detail sim-limits">软限位 —</p>
        <form class="sim-toolbar">
          <input type="number" step="any" required aria-label="${name}目标位置（米）" placeholder="目标 m" disabled>
          <button type="submit" data-action="move" disabled>移动</button>
          <button type="button" data-action="jog" data-direction="-1" aria-label="${name}负向点动 0.5 米" disabled>−0.5 m</button>
          <button type="button" data-action="jog" data-direction="1" aria-label="${name}正向点动 0.5 米" disabled>+0.5 m</button>
        </form>
      </section>`).join('')}
    <section>
      <h2>最近动作</h2>
      <p class="sim-detail sim-operation">等待调试指令</p>
      <progress max="1" value="0" aria-label="调试动作进度" hidden></progress>
    </section>`;
  document.body.append(panel);
  const notice = panel.querySelector('.sim-notice');
  const operationText = panel.querySelector('.sim-operation');
  const progress = panel.querySelector('progress');
  const stop = panel.querySelector('[data-action="stop"]');
  const focus = panel.querySelector('[data-action="focus"]');
  const scan = panel.querySelector('[data-action="capture_pointcloud"]');
  const scanView = panel.querySelector('[data-action="scan_view"]');
  const scanRange = panel.querySelector('#sim-scan-range');
  const mountSummary = panel.querySelector('.sim-mount-summary');
  const mounts = panel.querySelector('.sim-mounts');
  const scanStatus = panel.querySelector('.sim-scan-status');
  const scanResult = panel.querySelector('.sim-scan-result');
  const downloads = panel.querySelector('.sim-downloads');
  const preview = pointcloudPreview(panel.querySelector('.sim-pointcloud-preview'));
  const deviceButtons = [...panel.querySelectorAll('[data-equipment]')];
  const mountNames = {
    yard: '箱区俯扫', perimeter: '周边斜扫',
    yard_column: '箱区侧内扫', lane_column: '车道侧内扫',
    yard_column_cross: '箱区侧交叉扫', lane_column_cross: '车道侧交叉扫',
    yard_column_out: '箱区侧外扫', lane_column_out: '车道侧外扫',
    front_beam: '前横梁俯扫', rear_beam: '后横梁俯扫',
    front_spreader: '吊具前端近扫', rear_spreader: '吊具后端近扫',
    front_yard_beam_tip: '箱区侧前梁端补扫', rear_yard_beam_tip: '箱区侧后梁端补扫',
    yard_column_low: '箱区侧柱脚低位扫', lane_column_low: '车道侧柱脚低位扫',
    sts_front_port: '前端左角俯扫', sts_front_starboard: '前端右角俯扫',
    sts_front_center: '前端近垂直扫', sts_front_edge: '前端外缘斜扫',
    sts_rear_port: '后端左角俯扫', sts_rear_starboard: '后端右角俯扫',
    sts_rear_center: '后端近垂直扫', sts_rear_edge: '后端外缘斜扫',
  };
  const mountGroups = {trolley: '小车', column: '立柱', beam: '横梁', spreader: '吊具'};
  const mountFrames = {trolley: '小车', column: '大车', beam: '大车', spreader: '伸缩端'};
  const mountReturns = new Map();
  const rows = [...panel.querySelectorAll('[data-axis]')];
  let equipment = 'rtg';
  let state;
  let sending = false;
  let revision = 0;
  let pollError = '';
  let displayedCapture;

  function render() {
    const operation = state?.operation;
    const capture = state?.pointcloud;
    const blocked = !state || state.busy || sending || capture?.status === 'running';
    for (const button of deviceButtons) {
      button.disabled = sending;
      button.setAttribute('aria-pressed', String(button.dataset.equipment === equipment));
    }
    focus.textContent = `聚焦 ${devices[equipment]}`;
    focus.disabled = !state || sending;
    scan.disabled = blocked || !capture || capture.busy;
    scanRange.disabled = scan.disabled;
    scanView.textContent = equipment === 'sts01' ? '查看船箱区' : '查看箱区';
    scanView.disabled = !state || sending;
    stop.disabled = sending || operation?.status !== 'running';
    if (capture) {
      const statuses = {idle: '等待采集', running: '正在采集并导出点云…', completed: '采集完成', failed: '采集失败', cancelled: '采集已取消'};
      const text = statuses[capture.status] + (capture.error ? ' · ' + capture.error : '');
      if (scanStatus.textContent !== text) scanStatus.textContent = text;
      if (!mounts.children.length) {
        const groups = Object.entries(mountGroups).flatMap(([group, label]) => {
          const count = capture.mounts.filter(mount => mount.group === group).length;
          return count ? [`${label} ${count} 路`] : [];
        });
        mountSummary.textContent = `共 ${capture.mounts.length} 路 · ${groups.join(' · ')}`;
        for (const mount of capture.mounts) {
          const item = document.createElement('li');
          item.textContent = `${mountNames[mount.id]} · ${mountFrames[mount.group]}坐标 (${mount.offset_m.join(', ')}) m · 偏航 ${mount.yaw_deg}° · 下倾 ${mount.pitch_down_deg}°`;
          const count = document.createElement('span');
          mountReturns.set(mount.id, count);
          item.append(count);
          mounts.append(item);
        }
      }
      const result = capture.result;
      if (displayedCapture !== result?.files.ply) {
        displayedCapture = result?.files.ply;
        for (const [id, count] of mountReturns) {
          const returns = result?.returns[id];
          const ends = result?.end_face_scanners?.[id];
          count.textContent = returns === undefined ? '' : ` · 回波 ${returns}${ends ? ` · 箱端面 ${ends}` : ''}`;
        }
        downloads.replaceChildren();
        scanResult.hidden = downloads.hidden = !result;
        if (result) {
          const counts = result.point_counts;
          const ends = result.end_faces;
          const coverage = result.mode === 'ship'
            ? `船上箱区 ${counts.containers.toLocaleString('zh-CN')} · 甲板与船体 ${counts.surroundings.toLocaleString('zh-CN')}\n箱顶回波 ${result.ship_faces.top} · 箱侧回波 ${result.ship_faces.side}`
            : `RTG ${counts.rtg.toLocaleString('zh-CN')} · 箱体 ${counts.containers.toLocaleString('zh-CN')} · 周边 ${counts.surroundings.toLocaleString('zh-CN')}\n箱端面回波 +Y ${ends.positive_y} / −Y ${ends.negative_y}`;
          scanResult.textContent = `${result.point_count.toLocaleString('zh-CN')} 点 · ${result.range_m} m · 世界坐标（米）\n${coverage}`;
          for (const [extension, url] of Object.entries(result.files)) {
            const link = document.createElement('a');
            link.href = url;
            link.download = '';
            link.textContent = '下载 ' + extension.toUpperCase();
            downloads.append(link);
          }
        }
        preview.show(result);
      }
    }
    for (const row of rows) {
      const axis = row.dataset.axis;
      const position = state?.positions_m[axis];
      const limits = state?.limits[axis];
      row.querySelector('output').textContent = metres(position);
      const limitLabel = equipment === 'sts01' ? '限位' : '软限位';
      row.querySelector('.sim-limits').textContent = limits ? `${limitLabel} ${metres(limits[0])} ～ ${metres(limits[1])}` : `${limitLabel} —`;
      const input = row.querySelector('input');
      input.disabled = blocked;
      row.querySelector('[data-action="move"]').disabled = blocked;
      if (limits) [input.min, input.max] = limits;
      for (const button of row.querySelectorAll('[data-action="jog"]')) {
        const target = position + Number(button.dataset.direction) * 0.5;
        button.disabled = blocked || !limits || target < limits[0] || target > limits[1];
      }
    }
    progress.hidden = operation?.status !== 'running';
    if (!operation) {
      operationText.textContent = state?.busy ? `${devices[equipment]} 正在执行其他动作` : '等待调试指令';
      return;
    }
    const {axis, status, start, target, positions_m: arrived} = operation;
    const delta = target[axis] - start[axis];
    const direction = delta === 0 ? '原位' : axis === 'hoist_m' ? (delta > 0 ? '上升' : '下降') : (delta > 0 ? '正向' : '负向');
    const result = arrived ? ` · 最终位置 ${metres(arrived[axis])} · 偏差 ${metres(arrived[axis] - target[axis])}` : '';
    operationText.textContent = `${labels[status]} · ${axes[axis]}${direction} · ${metres(start[axis])} → ${metres(target[axis])}${result}${operation.error ? ' · ' + operation.error : ''}`;
    progress.value = operation.progress;
  }

  async function request(path, payload) {
    const options = payload ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)} : {};
    const response = await fetch('/api/sim/' + path, {...options, cache: 'no-store'});
    if (response.status === 401) location.replace('/login');
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '调试工作台暂不可用');
    return data;
  }

  async function send(payload) {
    if (sending) return;
    sending = true;
    revision += 1;
    notice.textContent = '';
    render();
    try {
      state = await request('command', {equipment, ...payload});
    } catch (error) {
      notice.textContent = error.message;
    } finally {
      sending = false;
      render();
    }
  }

  for (const row of rows) {
    const axis = row.dataset.axis;
    row.querySelector('form').addEventListener('submit', event => {
      event.preventDefault();
      send({action: 'move', axis, target_m: row.querySelector('input').valueAsNumber});
    });
  }
  for (const button of deviceButtons) {
    button.addEventListener('click', () => {
      if (sending || equipment === button.dataset.equipment) return;
      equipment = button.dataset.equipment;
      revision += 1;
      state = undefined;
      displayedCapture = undefined;
      notice.textContent = '';
      scanStatus.textContent = '正在读取设备状态…';
      mountSummary.textContent = '';
      mounts.replaceChildren();
      mountReturns.clear();
      scanResult.hidden = downloads.hidden = true;
      preview.show(undefined);
      render();
    });
  }
  panel.addEventListener('click', event => {
    const button = event.target.closest('button[data-action]');
    if (!button || button.type === 'submit') return;
    const {action, direction} = button.dataset;
    if (action === 'jog') {
      send({action, axis: button.closest('[data-axis]').dataset.axis, direction: Number(direction)});
    } else if (action === 'capture_pointcloud') {
      send({action, range_m: Number(scanRange.value)});
    } else {
      send({action});
    }
  });
  async function refresh() {
    if (!sending) {
      const requestedAt = revision;
      try {
        const next = await request('state?equipment=' + equipment);
        if (requestedAt === revision) {
          state = next;
          if (notice.textContent === pollError) notice.textContent = '';
          pollError = '';
          render();
        }
      } catch (error) {
        if (requestedAt === revision) {
          state = undefined;
          notice.textContent = pollError = error.message;
          render();
        }
      }
    }
    setTimeout(refresh, 500);
  }
  render();
  refresh();
}
