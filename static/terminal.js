// ─── GOLDMAN TERMINAL ─── shared JS

// Init lucide icons
if (window.lucide) {
  lucide.createIcons();
}

// ─── Footer time ───
function updateFooterTime() {
  const el = document.getElementById('footer-time');
  if (!el) return;
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  const hh = String(now.getHours()).padStart(2, '0');
  const min = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');
  el.textContent = `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss} GMT+8`;
}
setInterval(updateFooterTime, 1000);
updateFooterTime();

// ─── NAV time ───
function updateNavTime() {
  const el = document.getElementById('nav-time');
  if (!el) return;
  const now = new Date();
  el.textContent = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')} REFRESH`;
}
updateNavTime();
setInterval(updateNavTime, 30000);

// ─── Ticker bar ─── TWSE real-time + static fallback ───
async function loadTicker() {
  const el = document.getElementById('ticker-track');
  if (!el) return;

  // Fallback static data (TWSE closed or API fail)
  const fallback = [
    { symbol: '2330', name: '台積電', price: 1860, change_pct: 0 },
    { symbol: '2883', name: '凱基金', price: 21.5, change_pct: 0 },
    { symbol: '2891', name: '中信金', price: 55.8, change_pct: 0 },
    { symbol: 'GDX', name: 'Gold Miners', price: 94.97, change_pct: 1.23 },
  ];

  // 逐檔補值：API 回傳的用 API，缺的或 price<=0 的套 fallback
  let apiData = [];
  try {
    const res = await fetch('/api/ticker');
    if (res.ok) apiData = await res.json() || [];
  } catch (e) { /* network error → 全部用 fallback */ }

  const bySym = {};
  apiData.forEach(t => { if (t && t.symbol) bySym[t.symbol] = t; });
  const tickers = fallback.map(f => {
    const live = bySym[f.symbol];
    return (live && live.price > 0) ? live : f;
  });

  const renderItems = (items) => items.map(t => {
    const pct = t.change_pct || 0;
    const dir = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    const sign = pct > 0 ? '+' : '';
    const priceStr = t.price > 0 ? (Number.isInteger(t.price) ? t.price.toLocaleString() : t.price.toFixed(2)) : '—';
    const nameStr = t.name ? `<span class="ticker-name">${t.name}</span>` : '';
    return `<span class="ticker-item">
      <span class="ticker-symbol">${t.symbol}</span>
      ${nameStr}
      <span class="ticker-price">${priceStr}</span>
      <span class="ticker-change ${dir}">${sign}${pct.toFixed(2)}%</span>
    </span>`;
  }).join('');

  el.innerHTML = renderItems(tickers) + renderItems(tickers);
}

loadTicker();
setInterval(loadTicker, 30000);
async function loadNavStats() {
  const navEl = document.getElementById('nav-value');
  if (!navEl) return;

  try {
    const [navRes, posRes] = await Promise.all([
      fetch('/api/nav'),
      fetch('/api/positions')
    ]);
    const nav = await navRes.json();
    const positions = await posRes.json();

    const equityCost = nav.total_cost || 0;
    const cash = nav.cash || 0;
    const totalNav = equityCost + cash;

    navEl.textContent = 'NT$ ' + Math.round(totalNav).toLocaleString();
    document.getElementById('cash-value').textContent = 'NT$ ' + Math.round(cash).toLocaleString();
    document.getElementById('equity-value').textContent = 'NT$ ' + Math.round(equityCost).toLocaleString();
    document.getElementById('pos-count').textContent = (nav.positions_count || 0) + ' SYMBOLS';
  } catch (e) {
    navEl.textContent = '--';
  }
}

loadNavStats();
setInterval(loadNavStats, 60000);
