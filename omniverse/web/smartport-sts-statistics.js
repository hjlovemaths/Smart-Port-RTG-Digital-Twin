import {enablePanelDrag} from './smartport-panel-drag.js?v=1';

function drawPie(panel, report) {
  const pie = panel.querySelector('.agent-pie-disc');
  const legend = panel.querySelector('ul');
  const colors = ['#68d4df', '#f0b879', '#929efe', '#80d8a7', '#e887a6', '#78b8ef', '#d3d67d', '#bb92de'];
  const percent = new Intl.NumberFormat('zh-CN', {style: 'percent', maximumFractionDigits: 2});
  const values = new Intl.NumberFormat('zh-CN', {maximumSignificantDigits: 15});
  const stops = [];
  let end = 0;
  let lastColor;
  legend.replaceChildren();
  for (const [index, point] of report.series.entries()) {
    const color = colors[index % colors.length];
    const start = end;
    end += point.share * 100;
    if (point.share > 0) {
      stops.push(`${color} ${Math.min(100, start)}% ${Math.min(100, end)}%`);
      lastColor = color;
    }
    const row = document.createElement('li');
    const marker = document.createElement('span');
    marker.style.backgroundColor = color;
    marker.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.textContent = point.label;
    const value = document.createElement('strong');
    value.textContent = [values.format(point.value), report.unit, `(${percent.format(point.share)})`].filter(Boolean).join(' ');
    row.append(marker, label, value);
    legend.append(row);
  }
  pie.style.background = `conic-gradient(${stops.join(',')}, ${lastColor} 100%)`;
  panel.hidden = false;
}

function drawTrend(svg, report) {
  svg.replaceChildren();
  const points = report.series;
  const values = points.map(p => p.value).filter(v => v !== null);
  if (!values.length) return;
  svg.removeAttribute('hidden');
  function add(tag, attributes, label) {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (label !== undefined) node.textContent = label;
    svg.append(node);
    return node;
  }
  const min = Math.min(...values), max = Math.max(...values);
  const scale = Math.max(Math.abs(min), Math.abs(max), Number.MIN_VALUE);
  const range = max / scale - min / scale;
  const x = i => 108 + 600 * i / Math.max(1, points.length - 1);
  const y = value => range ? 160 - 135 * (value / scale - min / scale) / range : 92;
  const numbers = new Intl.NumberFormat('zh-CN', {maximumSignificantDigits: 6});
  add('title', {}, report.title);
  for (const value of new Set([min, max])) {
    add('line', {x1: 105, x2: 710, y1: y(value), y2: y(value), stroke: '#416274'});
    add('text', {x: 100, y: y(value) + 4, 'text-anchor': 'end'}, numbers.format(value));
  }
  let path = '', contiguous = false;
  points.forEach((point, i) => {
    if (point.value === null) {
      contiguous = false;
      return;
    }
    path += `${contiguous ? 'L' : 'M'}${x(i)} ${y(point.value)} `;
    contiguous = true;
  });
  add('path', {d: path, fill: 'none', stroke: '#74d4df', 'stroke-width': 2});
  points.forEach((point, i) => {
    if (point.value === null) return;
    const dot = add('circle', {cx: x(i), cy: y(point.value), r: 3, fill: '#9be1e9'});
    const hint = document.createElementNS(svg.namespaceURI, 'title');
    hint.textContent = `${point.label} · ${point.value} ${report.unit}`;
    dot.append(hint);
  });
  add('text', {x: 108, y: 192}, points[0].label.slice(0, 32));
  if (points.length > 1) add('text', {x: 708, y: 192, 'text-anchor': 'end'}, points.at(-1).label.slice(0, 32));
}

export function createStsStatisticsPanel(api, {analysis = false} = {}) {
  const route = analysis ? 'sts-analysis' : 'sts-statistics';
  const titleId = `${route}-title`;
  const defaultTitle = analysis ? '桥吊作业分析' : '作业流程效率统计报表(新)-装卸船';
  const element = document.createElement('section');
  element.id = `web-${route}`;
  element.className = 'sts-report-panel';
  element.hidden = true;
  element.setAttribute('aria-labelledby', titleId);
  element.innerHTML = `
    <header class="sts-statistics-header">
      <div><p>${analysis ? '桥吊 / Agent 分析' : '桥吊 / 数据统计'}</p><h2 id="${titleId}">${defaultTitle}</h2></div>
      <button type="button" class="sts-statistics-close">关闭</button>
    </header>
    ${analysis ? '<nav class="agent-report-picker" aria-label="本次生成的报表" hidden></nav>' : ''}
    <div class="sts-statistics-controls" hidden>
      <div class="sts-statistics-range">
        <strong>${analysis ? '' : '今天昼夜'}</strong>
        <span><time></time><span aria-hidden="true"> → </span><time></time> <small>北京时间</small></span>
      </div>
      ${analysis ? '' : '<label>桥吊 <select aria-label="桥吊编号"></select></label><button type="button" class="sts-statistics-query">查询</button>'}
    </div>
    <p class="sts-statistics-status" role="status"></p>
    ${analysis ? '<svg class="agent-data-trend" viewBox="0 0 730 205" role="img" hidden></svg>' : ''}
    ${analysis ? '<div class="agent-pie" hidden><div class="agent-pie-disc" aria-hidden="true"></div><ul aria-label="类别与占比" tabindex="0"></ul></div>' : ''}
    <div class="sts-statistics-table" tabindex="0" aria-label="${analysis ? '分析结果' : '装卸船作业明细'}，可横向滚动" hidden>
      <table aria-labelledby="${titleId}"><thead></thead><tbody></tbody></table>
    </div>
    ${analysis ? `<ul class="sts-analysis-chart" aria-labelledby="${titleId}" tabindex="0" hidden></ul>` : ''}`;
  document.body.append(element);
  const drag = enablePanelDrag(element, element.querySelector('.sts-statistics-header'));

  const controls = element.querySelector('.sts-statistics-controls');
  const title = element.querySelector('h2');
  const scope = controls.querySelector('strong');
  const select = controls.querySelector('select');
  const [start, end] = controls.querySelectorAll('time');
  const query = controls.querySelector('button');
  const status = element.querySelector('[role=status]');
  const scroller = element.querySelector('.sts-statistics-table');
  const head = scroller.querySelector('thead');
  const body = scroller.querySelector('tbody');
  const chart = element.querySelector('.sts-analysis-chart');
  const trend = element.querySelector('.agent-data-trend');
  const pie = element.querySelector('.agent-pie');
  const reportPicker = element.querySelector('.agent-report-picker');
  const number = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2});
  let activeRevision = null, dismissedRevision = null;
  let selectedReport = null;

  function renderChart(report) {
    const values = report.series.map(point => point.value ?? 0);
    const scale = Math.max(1e-300, ...values.map(Math.abs));
    const low = Math.min(0, ...values) / scale, high = Math.max(0, ...values) / scale;
    const span = high - low || 1;
    for (const point of report.series) {
      const item = document.createElement('li');
      const label = document.createElement('div');
      const group = document.createElement('strong');
      const detail = document.createElement('span');
      group.textContent = point.label;
      detail.textContent = point.detail;
      label.append(group, detail);
      const track = document.createElement('span');
      const bar = document.createElement('span');
      track.className = 'sts-analysis-track';
      track.setAttribute('aria-hidden', 'true');
      const scaled = (point.value ?? 0) / scale;
      bar.style.marginLeft = `${100 * (Math.min(0, scaled) - low) / span}%`;
      bar.style.width = `${100 * Math.abs(scaled) / span}%`;
      if (scaled < 0) bar.style.backgroundColor = '#e39a76';
      track.append(bar);
      const value = document.createElement('strong');
      value.textContent = point.value === null ? '无有效值' : `${number.format(point.value)} ${report.unit}`;
      item.append(label, track, value);
      chart.append(item);
    }
    chart.hidden = report.series.length === 0;
    chart.scrollTop = 0;
  }

  function renderReport(report) {
    const dataset = report.view === 'dataset';
    controls.hidden = dataset;
    if (!dataset) {
      start.dateTime = report.start;
      end.dateTime = report.end;
      start.textContent = report.start.slice(0, 16).replace('T', ' ');
      end.textContent = report.end.slice(0, 16).replace('T', ' ');
    }
    const coverage = report.missing_replies ? ` · ${report.missing_replies} 次请求无可用明细` : '';
    if (analysis) {
      title.textContent = report.title;
      element.querySelector('header p').textContent = dataset ? '数据 / Agent 分析' : '桥吊 / Agent 分析';
      element.classList.toggle('sts-analysis-ranking', report.view === 'ranking');
      if (dataset) {
        status.textContent = `${report.source} · 数据源 ${report.input_rows} 行 · ${report.truncated ? '仅显示前 ' : '结果 '}${report.rows.length} 行`;
      } else {
        scope.textContent = report.scope;
        const {row_count, valid_count, invalid_count} = report.overall;
        const details = invalid_count ? ` · ${invalid_count} 条用时缺失或无效` : '';
        const result = report.rows.length ? ` · 显示 ${report.rows.length} 条结果` : ' · 当前筛选无可展示结果';
        status.textContent = `${report.source} · ${row_count} 条作业 · ${valid_count} 条有效样本${details}${result}${coverage}`;
      }
      if (report.format === 'chart') {
        renderChart(report);
        drag.constrain();
        return;
      }
      if (report.format === 'line') drawTrend(trend, report);
      if (report.format === 'pie') {
        drawPie(pie, report);
        drag.constrain();
        return;
      }
      if (!report.rows.length) return;
    } else {
      select.replaceChildren(new Option('全部', 'ALL'), ...report.cranes.map(crane => new Option(crane, crane)));
      select.value = report.crane;
      status.textContent = `${report.source} · ${report.row_count} 条明细${coverage}`;
    }
    scroller.hidden = false;

    const header = document.createElement('tr');
    for (const name of report.columns) {
      const cell = document.createElement('th');
      cell.scope = 'col';
      cell.textContent = name;
      header.append(cell);
    }
    head.replaceChildren(header);
    const rows = document.createDocumentFragment();
    for (const values of report.rows) {
      const row = document.createElement('tr');
      for (const value of values) row.insertCell().textContent = value ?? (analysis ? '—' : '');
      rows.append(row);
    }
    body.replaceChildren(rows);
    scroller.scrollTo(0, 0);
    if (analysis) drag.constrain();
  }

  function resetReport() {
    title.textContent = defaultTitle;
    controls.hidden = true;
    scroller.hidden = true;
    if (chart) {
      trend.setAttribute('hidden', '');
      pie.hidden = true;
      chart.hidden = true;
      chart.replaceChildren();
    }
    head.replaceChildren();
    body.replaceChildren();
  }

  function showReports(report) {
    if (!reportPicker) {
      renderReport(report);
      return;
    }
    const reports = report.reports ?? [report];
    const selected = reports.find(item => item.revision === selectedReport) ?? reports[0];
    function selectReport(item) {
      selectedReport = item.revision;
      for (const button of reportPicker.children) {
        button.setAttribute('aria-pressed', String(button.dataset.revision === selectedReport));
      }
      resetReport();
      renderReport(item);
      drag.constrain();
    }
    reportPicker.replaceChildren(...reports.map(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = item.title;
      button.dataset.revision = item.revision;
      button.onclick = () => selectReport(item);
      return button;
    }));
    reportPicker.hidden = reports.length < 2;
    selectReport(selected);
  }

  function update(revision) {
    if (revision === activeRevision || (revision && revision === dismissedRevision)) return;
    activeRevision = revision;
    element.hidden = !revision;
    resetReport();
    if (reportPicker) reportPicker.hidden = true;
    if (!revision) {
      selectedReport = null;
      drag.cancel();
      return;
    }
    drag.constrain();
    status.textContent = '正在读取报表…';
    api(route).then(report => {
      if (activeRevision === revision && report.revision === revision) showReports(report);
    }).catch(error => {
      if (activeRevision === revision) {
        status.textContent = error.message;
      }
    });
  }

  if (query) query.onclick = async () => {
    query.disabled = true;
    const revision = activeRevision;
    try {
      const result = await api(route, {action: 'query', period: 'today_day_night', crane: select.value});
      if (activeRevision === revision) update(result.revision);
    } catch (error) {
      if (activeRevision === revision) status.textContent = error.message;
    } finally {
      query.disabled = false;
    }
  };
  element.querySelector('.sts-statistics-close').onclick = async () => {
    const revision = activeRevision;
    dismissedRevision = revision;
    if (revision) update(null);
    else element.hidden = true;
    try {
      await api(route, {action: 'close', revision});
    } catch (error) {
      if (dismissedRevision === revision && !activeRevision) console.warn('关闭桥吊统计报表失败', error);
    }
  };

  return {element, update};
}
