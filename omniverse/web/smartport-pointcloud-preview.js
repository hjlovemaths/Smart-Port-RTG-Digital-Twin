export function pointcloudPreview(figure) {
  const canvas = figure.querySelector('canvas');
  const legend = figure.querySelector('.sim-preview-legend');
  const status = figure.querySelector('.sim-preview-status');
  const resetButton = figure.querySelector('.sim-preview-reset');
  const context = canvas.getContext('2d');
  let cloud;
  let pending;
  let frame = 0;
  let camera;
  let drag;

  function reset() {
    camera = {yaw: -.72, tilt: .57, zoom: 1, x: 0, y: 0};
    repaint();
  }

  function repaint() {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  function draw() {
    frame = 0;
    if (!cloud || !context) return;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const {radius, verticalSpan, center, points, indices, colors} = cloud;
    const scale = .87 * Math.min(width / (radius * 2), height / (radius * 1.45 + verticalSpan)) * camera.zoom;
    const originX = width / 2 + camera.x;
    const originY = height / 2 + camera.y;
    const cosYaw = Math.cos(camera.yaw);
    const sinYaw = Math.sin(camera.yaw);
    const cosTilt = Math.cos(camera.tilt);
    const sinTilt = Math.sin(camera.tilt);
    for (const [group, list] of indices.entries()) {
      context.fillStyle = colors[group];
      const size = group === 0 ? 1.05 : 1.6;
      for (const i of list) {
        const x = points[i] - center[0];
        const y = points[i + 1] - center[1];
        const z = points[i + 2] - center[2];
        const u = originX + (x * cosYaw - y * sinYaw) * scale;
        const v = originY - ((x * sinYaw + y * cosYaw) * sinTilt + z * cosTilt) * scale;
        if (u >= 0 && v >= 0 && u < width && v < height) context.fillRect(u, v, size, size);
      }
    }
  }

  function unpack(buffer, count, palette) {
    if (!count || buffer.byteLength !== count * 13) throw new Error('点云预览数据不完整');
    const points = new Float32Array(buffer, 0, count * 3);
    const labels = new Uint8Array(buffer, count * 12, count);
    const lower = [Infinity, Infinity, Infinity];
    const upper = [-Infinity, -Infinity, -Infinity];
    const indices = palette.map(() => []);
    for (let i = 0; i < count; i++) {
      const start = i * 3;
      indices[labels[i]].push(start);
      for (let axis = 0; axis < 3; axis++) {
        const value = points[start + axis];
        lower[axis] = Math.min(lower[axis], value);
        upper[axis] = Math.max(upper[axis], value);
      }
    }
    return {
      points, indices,
      colors: palette.map(color => `rgb(${color.join(',')})`),
      center: lower.map((value, axis) => (value + upper[axis]) / 2),
      radius: Math.max(.01, Math.hypot(upper[0] - lower[0], upper[1] - lower[1]) / 2),
      verticalSpan: upper[2] - lower[2],
    };
  }

  async function show(result) {
    pending?.abort();
    if (!result?.preview || !context) {
      figure.hidden = true;
      return;
    }
    const request = new AbortController();
    pending = request;
    cloud = undefined;
    canvas.hidden = true;
    resetButton.disabled = true;
    figure.hidden = false;
    status.textContent = '正在加载合并点云…';
    try {
      const response = await fetch(result.preview.url, {signal: request.signal});
      if (!response.ok) throw new Error('点云预览暂时无法加载');
      const buffer = await response.arrayBuffer();
      if (request.signal.aborted) return;
      cloud = unpack(buffer, result.point_count, result.preview.colors);
      const ship = result.mode === 'ship';
      const labels = ship ? ['甲板与船体', '船上箱区'] : ['周边', '箱体', 'RTG'];
      canvas.setAttribute('aria-label', ship ? 'STS_01 采集的船箱区点云' : '主 RTG 和箱区的合并点云');
      for (const [index, item] of [...legend.children].entries()) {
        item.hidden = !labels[index];
        item.textContent = labels[index] || '';
        item.style.setProperty('--point-color', cloud.colors[index]);
      }
      status.textContent = '';
      canvas.hidden = false;
      resetButton.disabled = false;
      reset();
    } catch (error) {
      if (!request.signal.aborted) status.textContent = error.message;
    }
  }

  canvas.addEventListener('pointerdown', event => {
    if (!cloud || ![0, 1].includes(event.button)) return;
    canvas.setPointerCapture(event.pointerId);
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, pan: event.shiftKey || event.button === 1};
    event.preventDefault();
  });
  canvas.addEventListener('pointermove', event => {
    if (drag?.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    drag.x = event.clientX;
    drag.y = event.clientY;
    if (drag.pan) {
      camera.x += dx;
      camera.y += dy;
    } else {
      camera.yaw += dx * .012;
      camera.tilt = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, camera.tilt + dy * .012));
    }
    repaint();
  });
  canvas.addEventListener('lostpointercapture', () => { drag = undefined; });
  canvas.addEventListener('wheel', event => {
    if (!cloud) return;
    event.preventDefault();
    camera.zoom = Math.max(.55, Math.min(12, camera.zoom * Math.exp(-event.deltaY * .0015)));
    repaint();
  }, {passive: false});
  resetButton.addEventListener('click', reset);
  new ResizeObserver(repaint).observe(canvas);
  reset();
  return {show};
}
