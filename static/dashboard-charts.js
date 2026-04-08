// ─── Dashboard ApexCharts configs ───────────────────────────
// 三個圖：NAV trajectory (area, dual-line) / Sector donut / Drift bar

const CHART_PALETTE = ['#2563eb', '#9ca3af', '#16a34a', '#dc2626', '#f59e0b', '#8b5cf6', '#06b6d4'];

function fmtTwd(n) {
  if (n == null || isNaN(n)) return '—';
  return 'NT$' + Math.round(n).toLocaleString();
}

function fmtPct(n, digits = 2) {
  if (n == null || isNaN(n)) return '—';
  const sign = n > 0 ? '+' : '';
  return sign + n.toFixed(digits) + '%';
}

// ─── NAV trajectory (area chart, dual line) ─────────────
let navChartInstance = null;
function renderNavChart(rows, selectedDays) {
  const host = document.getElementById('nav-chart');
  if (!host) return;
  if (!rows || rows.length === 0) {
    host.innerHTML = '<div class="chart-empty">尚無 nav_history 資料 — 跑 daily_snapshot.py 後就會出現</div>';
    return;
  }
  // Normalize TWII so the first point matches NAV's first point.
  // 用意：兩條線同基準，視覺上一眼看出跑贏 / 跑輸。
  const navSeries = rows.map(r => ({ x: r.date, y: Math.round(r.total_value) }));
  let twiiSeries = [];
  const firstNav = rows.find(r => r.total_value)?.total_value;
  const firstTwii = rows.find(r => r.twii_close)?.twii_close;
  if (firstNav && firstTwii) {
    twiiSeries = rows
      .filter(r => r.twii_close)
      .map(r => ({ x: r.date, y: Math.round(firstNav * (r.twii_close / firstTwii)) }));
  }

  const series = [{ name: 'NAV', data: navSeries }];
  if (twiiSeries.length) series.push({ name: '^TWII (對齊起點)', data: twiiSeries });

  const opts = {
    chart: {
      type: 'area',
      height: 320,
      toolbar: { show: false },
      zoom: { enabled: false },
      animations: { enabled: true, easing: 'easeout', speed: 400 },
      fontFamily: 'DM Sans, sans-serif',
    },
    colors: ['#2563eb', '#9ca3af'],
    series,
    stroke: { curve: 'smooth', width: [2.5, 1.5], dashArray: [0, 4] },
    fill: {
      type: 'gradient',
      gradient: {
        opacityFrom: [0.25, 0.0],
        opacityTo: [0.02, 0.0],
        stops: [0, 100],
      },
    },
    xaxis: {
      type: 'datetime',
      labels: { style: { fontSize: '11px', colors: '#6b7280' } },
      axisBorder: { show: false },
      axisTicks: { show: false },
    },
    yaxis: {
      labels: {
        style: { fontSize: '11px', colors: '#6b7280' },
        formatter: (v) => 'NT$' + (v >= 10000 ? (v / 10000).toFixed(0) + '萬' : v.toLocaleString()),
      },
    },
    grid: { borderColor: 'rgba(0,0,0,0.05)', strokeDashArray: 4 },
    tooltip: {
      shared: true,
      x: { format: 'yyyy-MM-dd' },
      y: { formatter: (v) => fmtTwd(v) },
    },
    legend: { position: 'top', horizontalAlign: 'right', fontSize: '11px' },
    dataLabels: { enabled: false },
  };

  if (navChartInstance) {
    navChartInstance.updateOptions(opts, true, true);
  } else {
    host.innerHTML = '';
    navChartInstance = new ApexCharts(host, opts);
    navChartInstance.render();
  }
}

// ─── Sector donut ────────────────────────────────────────
let sectorChartInstance = null;
function renderSectorChart(bySector) {
  const host = document.getElementById('sector-chart');
  if (!host) return;
  if (!bySector || bySector.length === 0) {
    host.innerHTML = '<div class="chart-empty">無持股</div>';
    return;
  }
  const labels = bySector.map(s => s.sector);
  const data = bySector.map(s => s.pct);
  const opts = {
    chart: { type: 'donut', height: 280, fontFamily: 'DM Sans, sans-serif' },
    colors: CHART_PALETTE,
    series: data,
    labels,
    legend: { position: 'bottom', fontSize: '11px', markers: { width: 10, height: 10 } },
    dataLabels: {
      enabled: true,
      formatter: (v) => v.toFixed(1) + '%',
      style: { fontSize: '11px', fontWeight: 600 },
      dropShadow: { enabled: false },
    },
    plotOptions: {
      pie: {
        donut: {
          size: '62%',
          labels: {
            show: true,
            total: {
              show: true, label: '產業數', fontSize: '11px',
              formatter: () => labels.length + ' 類',
            },
          },
        },
      },
    },
    tooltip: { y: { formatter: (v) => v.toFixed(1) + '%' } },
  };
  if (sectorChartInstance) {
    sectorChartInstance.updateOptions(opts, true, true);
  } else {
    host.innerHTML = '';
    sectorChartInstance = new ApexCharts(host, opts);
    sectorChartInstance.render();
  }
}

// ─── Drift horizontal bar ────────────────────────────────
let driftChartInstance = null;
function renderDriftChart(drift) {
  const host = document.getElementById('drift-chart');
  if (!host) return;
  if (!drift || drift.length === 0) {
    host.innerHTML = '<div class="chart-empty">尚未設定 allocation_targets<br><span style="font-size:11px">用 SQL INSERT 設定後重整</span></div>';
    return;
  }
  // 排序：偏離最大的在上
  const sorted = [...drift].sort((a, b) => Math.abs(b.diff_pct) - Math.abs(a.diff_pct));
  const categories = sorted.map(d => `${d.scope}/${d.key}`);
  const series = [{
    name: 'diff vs 目標',
    data: sorted.map(d => ({
      x: `${d.scope}/${d.key}`,
      y: d.diff_pct,
      fillColor: d.diff_pct > 1 ? '#dc2626' : d.diff_pct < -1 ? '#16a34a' : '#9ca3af',
    })),
  }];
  const opts = {
    chart: { type: 'bar', height: 280, toolbar: { show: false }, fontFamily: 'DM Sans, sans-serif' },
    series,
    plotOptions: {
      bar: {
        horizontal: true,
        barHeight: '60%',
        distributed: false,
        colors: { ranges: [] },
        dataLabels: { position: 'center' },
      },
    },
    xaxis: {
      type: 'category',
      categories,
      labels: { style: { fontSize: '10px', colors: '#6b7280' }, formatter: (v) => v + '%' },
      axisBorder: { show: false },
      axisTicks: { show: false },
    },
    yaxis: { labels: { style: { fontSize: '11px', colors: '#374151' } } },
    grid: { borderColor: 'rgba(0,0,0,0.05)', strokeDashArray: 4 },
    dataLabels: {
      enabled: true,
      formatter: (v, opts) => {
        const d = sorted[opts.dataPointIndex];
        return `${d.current_pct.toFixed(1)}% / ${d.target_pct.toFixed(0)}%`;
      },
      style: { fontSize: '10px', colors: ['#fff'] },
    },
    tooltip: {
      custom: ({ dataPointIndex }) => {
        const d = sorted[dataPointIndex];
        return `<div style="padding:8px 12px;font-size:12px">
          <div style="font-weight:700">${d.scope} / ${d.key}</div>
          <div>目前 ${d.current_pct.toFixed(2)}%</div>
          <div>目標 ${d.target_pct.toFixed(0)}%</div>
          <div style="margin-top:4px;font-weight:700;color:${d.diff_pct > 0 ? '#dc2626' : '#16a34a'}">
            ${d.diff_pct > 0 ? '+' : ''}${d.diff_pct.toFixed(2)}% → ${d.action}
          </div>
        </div>`;
      },
    },
  };
  if (driftChartInstance) {
    driftChartInstance.updateOptions(opts, true, true);
  } else {
    host.innerHTML = '';
    driftChartInstance = new ApexCharts(host, opts);
    driftChartInstance.render();
  }
}
