export function enablePanelDrag(panel, handle) {
  let drag = null;

  function place(left, top) {
    const {width, height} = panel.getBoundingClientRect();
    const {clientWidth, clientHeight} = document.documentElement;
    panel.style.transform = 'none';
    panel.style.left = `${Math.max(0, Math.min(left, clientWidth - width))}px`;
    panel.style.top = `${Math.max(0, Math.min(top, clientHeight - height))}px`;
  }

  function constrain() {
    if (panel.hidden || !panel.style.left) return;
    const {left, top} = panel.getBoundingClientRect();
    place(left, top);
  }

  function cancel() {
    if (drag && handle.hasPointerCapture(drag.id)) handle.releasePointerCapture(drag.id);
    drag = null;
    panel.classList.remove('panel-dragging');
  }

  handle.addEventListener('pointerdown', event => {
    if (!event.isPrimary || event.button !== 0 || event.target.closest('button')) return;
    const {left, top} = panel.getBoundingClientRect();
    handle.setPointerCapture(event.pointerId);
    drag = {id: event.pointerId, x: event.clientX - left, y: event.clientY - top};
    panel.classList.add('panel-dragging');
    event.preventDefault();
  });
  handle.addEventListener('pointermove', event => {
    if (drag?.id === event.pointerId) place(event.clientX - drag.x, event.clientY - drag.y);
  });
  handle.addEventListener('lostpointercapture', event => {
    if (drag?.id === event.pointerId) cancel();
  });
  window.addEventListener('resize', constrain);
  return {constrain, cancel};
}
