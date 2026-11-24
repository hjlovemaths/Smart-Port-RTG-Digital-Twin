import {enablePanelDrag} from './smartport-panel-drag.js?v=1';

export function createStsStatisticsPanel(api) {
  const element = document.createElement('section');
  element.id = 'web-sts-statistics';
  element.hidden = true;
  element.setAttribute('aria-labelledby', 'sts-statistics-title');
  element.innerHTML = `
    <header class="sts-statistics-header">
      <div><p>桥吊 / 数据统计</p><h2 id="sts-statistics-title">作业流程效率统计报表(新)-装卸船</h2></div>
      <button type="button" class="sts-statistics-close">关闭</button>
    </header>
    <div class="sts-statistics-controls" hidden>
      <div class="sts-statistics-range">
        <strong>今天昼夜</strong>
        <span><time></time><span aria-hidden="true"> → </span><time></time> <small>北京时间</small></span>
      </div>
      <label>桥吊 <select aria-label="桥吊编号"></select></label>
      <button type="button" class="sts-statistics-query">查询</button>
    </div>
    <p class="sts-statistics-status" role="status"></p>
    <div class="sts-statistics-table" tabindex="0" aria-label="装卸船作业明细，可横向滚动" hidden>
      <table aria-labelledby="sts-statistics-title"><thead></thead><tbody></tbody></table>
    </div>`;
  document.body.append(element);
  const drag = enablePanelDrag(element, element.querySelector('.sts-statistics-header'));

  const controls = element.querySelector('.sts-statistics-controls');
  const select = controls.querySelector('select');
  const [start, end] = controls.querySelectorAll('time');
  const query = controls.querySelector('button');
  const status = element.querySelector('[role=status]');
  const scroller = element.querySelector('.sts-statistics-table');
  const head = scroller.querySelector('thead');
  const body = scroller.querySelector('tbody');
  let activeRevision = null, dismissedRevision = null;

  function renderReport(report) {
    controls.hidden = false;
    start.dateTime = report.start;
    end.dateTime = report.end;
    start.textContent = report.start.slice(0, 16).replace('T', ' ');
    end.textContent = report.end.slice(0, 16).replace('T', ' ');
    select.replaceChildren(new Option('全部', 'ALL'), ...report.cranes.map(crane => new Option(crane, crane)));
    select.value = report.crane;

    const coverage = report.missing_replies ? ` · ${report.missing_replies} 次请求无可用明细` : '';
    status.textContent = `${report.source} · ${report.row_count} 条明细${coverage}`;
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
      for (const value of values) row.insertCell().textContent = value ?? '';
      rows.append(row);
    }
    body.replaceChildren(rows);
    scroller.scrollTo(0, 0);
  }

  function update(revision) {
    if (revision === activeRevision || (revision && revision === dismissedRevision)) return;
    activeRevision = revision;
    element.hidden = !revision;
    controls.hidden = true;
    scroller.hidden = true;
    head.replaceChildren();
    body.replaceChildren();
    if (!revision) {
      drag.cancel();
      return;
    }
    drag.constrain();
    status.textContent = '正在读取报表…';
    api('sts-statistics').then(report => {
      if (activeRevision === revision && report.revision === revision) renderReport(report);
    }).catch(error => {
      if (activeRevision === revision) {
        status.textContent = error.message;
      }
    });
  }

  query.onclick = async () => {
    query.disabled = true;
    const revision = activeRevision;
    try {
      const result = await api('sts-statistics', {action: 'query', period: 'today_day_night', crane: select.value});
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
      await api('sts-statistics', {action: 'close', revision});
    } catch (error) {
      if (dismissedRevision === revision && !activeRevision) console.warn('关闭桥吊统计报表失败', error);
    }
  };

  return {element, update};
}
