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

// ─── Ticker bar (mock for now, hook to FinMind/AlphaVantage later) ───
async function loadTicker() {
  const el = document.getElementById('ticker-track');
  if (!el) return;

  // Static mock data for now (terminal is pretty + functional)
  const tickers = [
    { symbol: 'TWII', price: '21,450.32', change: '+1.2%', dir: 'up' },
    { symbol: 'TSMC', price: '1,810', change: '-0.5%', dir: 'down' },
    { symbol: 'GDX', price: '$93.82', change: '-0.8%', dir: 'down' },
    { symbol: 'GOLD', price: '$4,799', change: '+0.4%', dir: 'up' },
    { symbol: 'BRENT', price: '$87.42', change: '+1.1%', dir: 'up' },
    { symbol: 'USDTWD', price: '31.45', change: '0.0%', dir: 'up' },
    { symbol: 'BTC', price: '$66,820', change: '-2.1%', dir: 'down' },
    { symbol: 'SP500', price: '5,653', change: '-0.3%', dir: 'down' },
    { symbol: 'NASDAQ', price: '17,890', change: '-0.7%', dir: 'down' },
    { symbol: '2891', price: '54.5', change: '+1.5%', dir: 'up' },
    { symbol: '2883', price: '19.9', change: '+0.5%', dir: 'up' },
    { symbol: '2303', price: '58.4', change: '+9.0%', dir: 'up' },
  ];

  const renderItems = (items) => items.map(t => `
    <span class="ticker-item">
      <span class="ticker-symbol">${t.symbol}</span>
      <span class="ticker-price">${t.price}</span>
      <span class="ticker-change ${t.dir}">${t.change}</span>
    </span>
  `).join('');

  // Duplicate for seamless scroll
  el.innerHTML = renderItems(tickers) + renderItems(tickers);
}

loadTicker();

// ─── NAV stats (homepage) ───
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
