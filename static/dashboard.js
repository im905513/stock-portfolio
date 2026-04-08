// ─── Dashboard data fetching + render orchestration ─────────

// 透過同源 /api/dashboard/* 取資料 (無需 token)
const API = {
  summary: '/api/dashboard/summary',
  navHist: (days) => `/api/nav/history?days=${days}`,
  positionsRt: '/api/positions/rt',
};

let currentNavDays = 90;
let currentSummary = null;

// ─── KPI cards ─────────────────────────────────────────
function renderKpis(summary) {
  const nav = summary.nav || {};
  const perf = summary.performance || {};
  const goal = summary.goal_2031 || {};

  // NAV
  const navCard = document.getElementById('kpi-nav');
  if (navCard) {
    const dod = perf['30d_pct'];
    navCard.querySelector('.kpi-value').textContent = fmtTwd(nav.total);
    navCard.querySelector('.kpi-sub').textContent = dod != null ? `30D ${fmtPct(dod)}` : '需要 ≥2 天 nav_history';
    navCard.classList.remove('up', 'down');
    if (dod > 0) navCard.classList.add('up');
    else if (dod < 0) navCard.classList.add('down');
  }

  // Cash %
  const cashCard = document.getElementById('kpi-cash');
  if (cashCard) {
    const cashPct = nav.total > 0 ? (nav.cash / nav.total * 100) : 0;
    cashCard.querySelector('.kpi-value').textContent = cashPct.toFixed(1) + '%';
    cashCard.querySelector('.kpi-sub').textContent = fmtTwd(nav.cash);
  }

  // CAGR
  const cagrCard = document.getElementById('kpi-cagr');
  if (cagrCard) {
    const cagr = perf.realized_cagr;  // ai_routes 沒給,goal endpoint 才有
    cagrCard.querySelector('.kpi-value').textContent = '尚未累積';
    cagrCard.querySelector('.kpi-sub').textContent = goal.implied_cagr_required
      ? `需要 ${(goal.implied_cagr_required * 100).toFixed(1)}% / 年達標`
      : '—';
  }

  // Goal 2031 gap
  const goalCard = document.getElementById('kpi-goal');
  if (goalCard) {
    goalCard.querySelector('.kpi-value').textContent = fmtTwd(goal.gap);
    goalCard.querySelector('.kpi-sub').textContent = goal.years_left ? `剩 ${goal.years_left} 年` : '—';
  }
}

// 補強 CAGR — 從 /api/goal/2031 拿 realized_cagr
async function refreshCagrFromGoal() {
  try {
    const r = await fetch('/api/goal/2031');
    if (!r.ok) return;
    const g = await r.json();
    const cagrCard = document.getElementById('kpi-cagr');
    if (!cagrCard) return;
    if (g.realized_cagr != null) {
      cagrCard.querySelector('.kpi-value').textContent = (g.realized_cagr * 100).toFixed(2) + '%';
      cagrCard.classList.remove('up', 'down');
      if (g.realized_cagr > 0) cagrCard.classList.add('up');
      else cagrCard.classList.add('down');
    }
    if (g.implied_cagr_required != null) {
      cagrCard.querySelector('.kpi-sub').textContent = `需要 ${(g.implied_cagr_required * 100).toFixed(1)}% / 年達標`;
    }
  } catch (e) { /* silent */ }
}

// ─── Alerts ─────────────────────────────────────────────
function renderAlerts(alerts) {
  const list = document.getElementById('alerts-list');
  const count = document.getElementById('alerts-count');
  if (!list) return;
  if (!alerts || alerts.length === 0) {
    list.innerHTML = '<div class="alert-empty ok">✓ 一切正常 — 沒有觸發中的警示</div>';
    if (count) count.textContent = '0';
    return;
  }
  if (count) count.textContent = alerts.length + ' 筆';
  list.innerHTML = alerts.map(a => {
    const sev = a.severity || 'info';
    return `<div class="alert-item severity-${sev}">
      <div class="alert-msg">${escapeHtml(a.message || a.metric)}</div>
      <div class="alert-meta">${escapeHtml(a.scope)}${a.target ? ' · ' + escapeHtml(a.target) : ''} · ${a.metric} ${a.op} ${a.threshold} (實際 ${a.actual})</div>
    </div>`;
  }).join('');
}

// ─── Top movers (今日動態) ────────────────────────────
function renderMovers(positions) {
  const list = document.getElementById('movers-list');
  if (!list) return;
  if (!positions || positions.length === 0) {
    list.innerHTML = '<div class="movers-empty">無持股</div>';
    return;
  }
  // 排序：abs(change_pct) 降序
  const sorted = [...positions]
    .filter(p => p.change_pct != null && p.change_pct !== 0)
    .sort((a, b) => Math.abs(b.change_pct) - Math.abs(a.change_pct))
    .slice(0, 6);
  if (sorted.length === 0) {
    list.innerHTML = '<div class="movers-empty">今日無顯著漲跌</div>';
    return;
  }
  list.innerHTML = sorted.map(p => {
    const dir = p.change_pct > 0 ? 'up' : 'down';
    const arrow = p.change_pct > 0 ? '▲' : '▼';
    return `<a class="mover-item ${dir}" href="/fundamentals?symbol=${encodeURIComponent(p.symbol)}" style="text-decoration:none;color:inherit">
      <div>
        <span class="mover-sym">${escapeHtml(p.symbol)}</span>
        <span class="mover-name">${escapeHtml(p.name || '')}</span>
      </div>
      <span class="mover-pct">${arrow} ${Math.abs(p.change_pct).toFixed(2)}%</span>
    </a>`;
  }).join('');
}

// ─── Discipline table (持股 thesis 檢查) ───────────────
async function renderDiscipline(positions) {
  const tbody = document.querySelector('#discipline-table tbody');
  if (!tbody) return;
  if (!positions || positions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">無持股</td></tr>';
    return;
  }

  // 並行抓每檔的 thesis (走 /api/dashboard/summary 已經有 portfolio,
  // 但 thesis 在 ai/stock 裡。為了同源無 token,我們需要一個無 token 的 stock context。
  // 先用簡化方案：只 render positions,thesis 欄留 -- 直到後端補一個無 token 的 stock 端口。)
  // 為避免新增更多後端,thesis 走 fetch /api/dashboard/stock-thesis (新增小 endpoint)

  let thesisMap = {};
  try {
    const r = await fetch('/api/dashboard/thesis');
    if (r.ok) thesisMap = await r.json();
  } catch (e) { /* silent — 表還是會顯示,只是 thesis 欄為 -- */ }

  tbody.innerHTML = positions.map(p => {
    const t = (thesisMap[p.symbol] || [])[0];  // 取最新一筆 active thesis
    const cur = p.current_price || 0;
    const target = t?.target_price;
    const stop = t?.stop_loss;
    const gapToTarget = (target && cur) ? ((target - cur) / cur * 100) : null;
    const gapToStop = (stop && cur) ? ((cur - stop) / cur * 100) : null;

    let statusTag = '<span class="status-tag unset">未設定</span>';
    let rowClass = 'unset';
    if (t) {
      rowClass = '';
      if (gapToStop != null && gapToStop < 5) {
        statusTag = '<span class="status-tag danger">接近停損</span>';
      } else if (gapToTarget != null && gapToTarget <= 0) {
        statusTag = '<span class="status-tag warn">已達目標</span>';
      } else {
        statusTag = '<span class="status-tag ok">紀律內</span>';
      }
    }

    const sym = p.currency === 'USD' ? '$' : 'NT$';
    return `<tr class="${rowClass}">
      <td><strong>${escapeHtml(p.symbol)}</strong></td>
      <td>${escapeHtml(p.name || '')}</td>
      <td class="num">${cur ? sym + cur.toFixed(2) : '—'}</td>
      <td class="num">${p.avg_cost ? sym + Number(p.avg_cost).toFixed(2) : '—'}</td>
      <td class="num ${p.pnl_pct > 0 ? 'pos-good' : p.pnl_pct < 0 ? 'pos-bad' : ''}">${p.pnl_pct != null ? fmtPct(p.pnl_pct) : '—'}</td>
      <td class="num">${target ? sym + target.toFixed(2) : '<span class="pos-muted">—</span>'}</td>
      <td class="num ${gapToTarget != null ? (gapToTarget > 0 ? 'pos-good' : 'pos-warn') : 'pos-muted'}">${gapToTarget != null ? fmtPct(gapToTarget) : '—'}</td>
      <td class="num">${stop ? sym + stop.toFixed(2) : '<span class="pos-muted">—</span>'}</td>
      <td class="num ${gapToStop != null ? (gapToStop < 5 ? 'pos-bad' : 'pos-good') : 'pos-muted'}">${gapToStop != null ? fmtPct(gapToStop) : '—'}</td>
      <td>${statusTag}</td>
    </tr>`;
  }).join('');
}

// ─── helpers ───────────────────────────────────────────
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// ─── NAV history loader ────────────────────────────────
async function loadNavChart(days) {
  try {
    const r = await fetch(API.navHist(days));
    if (!r.ok) throw new Error('http ' + r.status);
    const rows = await r.json();
    renderNavChart(rows, days);
  } catch (e) {
    const host = document.getElementById('nav-chart');
    if (host) host.innerHTML = '<div class="chart-empty">載入失敗</div>';
  }
}

// ─── Main loader ───────────────────────────────────────
async function loadAll() {
  // Summary (KPIs / alerts / sector / drift)
  try {
    const r = await fetch(API.summary);
    if (!r.ok) throw new Error('http ' + r.status);
    const summary = await r.json();
    currentSummary = summary;
    renderKpis(summary);
    renderAlerts(summary.alerts);
    renderSectorChart(summary.concentration?.by_sector);
    renderDriftChart(summary.drift);
    refreshCagrFromGoal();
  } catch (e) {
    document.getElementById('alerts-list').innerHTML = '<div class="alert-empty">summary 載入失敗</div>';
  }

  // Realtime positions for movers + discipline (帶 change_pct 即時)
  try {
    const r = await fetch(API.positionsRt);
    if (!r.ok) throw new Error('http ' + r.status);
    const positions = await r.json();
    renderMovers(positions);
    renderDiscipline(positions);
  } catch (e) {
    document.getElementById('movers-list').innerHTML = '<div class="movers-empty">載入失敗</div>';
  }
}

// ─── Range tabs wiring ─────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const tabs = document.querySelectorAll('#nav-range-tabs button');
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentNavDays = parseInt(btn.dataset.days, 10);
      loadNavChart(currentNavDays);
    });
  });

  loadAll();
  loadNavChart(currentNavDays);
  // 30s 重整 alerts + KPIs + movers
  setInterval(loadAll, 30_000);
  // 5min 重整 NAV chart
  setInterval(() => loadNavChart(currentNavDays), 5 * 60_000);
});
