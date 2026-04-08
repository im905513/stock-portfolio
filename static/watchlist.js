// watchlist.js — DCA candidate discovery + watchlist management
(function () {
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmt(n, digits) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toFixed(digits == null ? 2 : digits);
  }

  function scoreTag(s) {
    if (s >= 0.75) return `<span class="tag tag-tw">${fmt(s, 2)}</span>`;
    if (s >= 0.55) return `<span class="tag">${fmt(s, 2)}</span>`;
    return `<span class="tag tag-us">${fmt(s, 2)}</span>`;
  }

  async function loadMyWatchlist() {
    try {
      const res = await fetch('/api/dashboard/watchlist');
      const list = await res.json();
      document.getElementById('my-meta').textContent = list.length + ' STOCKS';
      if (!list.length) {
        document.getElementById('my-list').innerHTML = '<div class="empty-state">尚未加入任何追蹤股</div>';
        return;
      }
      let h = '<table class="data-table"><thead><tr><th>SYMBOL</th><th>NAME</th><th>SECTOR</th><th>MARKET</th><th>ADDED</th><th>ACTIONS</th></tr></thead><tbody>';
      for (const s of list) {
        const tag = s.market === 'TW' ? 'tag-tw' : 'tag-us';
        h += `<tr>
          <td><strong>${escapeHtml(s.symbol)}</strong></td>
          <td>${escapeHtml(s.name)}</td>
          <td>${escapeHtml(s.sector || '—')}</td>
          <td><span class="tag ${tag}">${escapeHtml(s.market)}</span></td>
          <td style="font-size:11px;opacity:.7">${escapeHtml((s.created_at || '').split('T')[0] || '')}</td>
          <td><button class="btn" onclick="removeWatch('${escapeHtml(s.symbol)}')">移除</button></td>
        </tr>`;
      }
      h += '</tbody></table>';
      document.getElementById('my-list').innerHTML = h;
    } catch (e) {
      document.getElementById('my-list').innerHTML = '<div class="empty-state">載入失敗: ' + escapeHtml(e.message) + '</div>';
    }
  }

  async function runDiscover(e) {
    e.preventDefault();
    const industry = document.getElementById('industry').value.trim();
    const min_score = document.getElementById('min_score').value || '0.5';
    const pool = document.getElementById('pool').value || '40';
    const msg = document.getElementById('discover-msg');
    msg.className = 'msg';
    msg.textContent = '> 挖掘中… 第一次最久 30 秒';
    msg.style.display = 'block';
    document.getElementById('cand-list').innerHTML = '<div class="empty-state">FETCHING FROM FINMIND...</div>';

    const params = new URLSearchParams({ min_score, pool, limit: '20' });
    if (industry) params.set('industry', industry);

    try {
      const res = await fetch('/api/dashboard/discover?' + params.toString());
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      msg.className = 'msg msg-ok';
      msg.textContent = `> 完成 — pool=${data.pool_size}, 符合 ${data.count}`;
      document.getElementById('discover-meta').textContent = `${data.market} · pool ${data.pool_size}`;
      document.getElementById('cand-meta').textContent = data.count + ' MATCHES';
      renderCandidates(data.candidates);
    } catch (err) {
      msg.className = 'msg msg-err';
      msg.textContent = '> 錯誤: ' + err.message;
      document.getElementById('cand-list').innerHTML = '<div class="empty-state">失敗</div>';
    }
    setTimeout(() => { msg.style.display = 'none'; }, 6000);
  }

  function renderCandidates(list) {
    if (!list || !list.length) {
      document.getElementById('cand-list').innerHTML = '<div class="empty-state">無符合條件的候選 — 試著降低 min_score 或更換產業</div>';
      return;
    }
    let h = '<table class="data-table"><thead><tr><th>SCORE</th><th>SYMBOL</th><th>NAME</th><th>INDUSTRY</th><th class="num">PE</th><th class="num">PBR</th><th class="num">YIELD%</th><th>REASONS</th><th>ACTIONS</th></tr></thead><tbody>';
    for (const c of list) {
      const reasons = (c.reasons || []).slice(0, 3).map(escapeHtml).join(' · ');
      const inWatch = c.in_watchlist;
      const btn = inWatch
        ? '<button class="btn" disabled style="opacity:.5">已追蹤</button>'
        : `<button class="btn" onclick="addWatch('${escapeHtml(c.symbol)}','${escapeHtml(c.name)}','${escapeHtml(c.industry || '')}')">加入追蹤</button>`;
      h += `<tr>
        <td>${scoreTag(c.score)}</td>
        <td><strong>${escapeHtml(c.symbol)}</strong></td>
        <td>${escapeHtml(c.name)}</td>
        <td style="font-size:11px;opacity:.8">${escapeHtml(c.industry || '—')}</td>
        <td class="num">${fmt(c.per, 1)}</td>
        <td class="num">${fmt(c.pbr, 2)}</td>
        <td class="num">${fmt(c.dividend_yield, 2)}</td>
        <td style="font-size:11px">${reasons}</td>
        <td>${btn}</td>
      </tr>`;
    }
    h += '</tbody></table>';
    document.getElementById('cand-list').innerHTML = h;
  }

  async function addWatch(symbol, name, sector) {
    try {
      const res = await fetch('/api/dashboard/watchlist/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, name, sector, market: 'TW' }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      await loadMyWatchlist();
      // 重跑當前候選，把該檔標成「已追蹤」
      const form = document.querySelector('form');
      if (form) form.dispatchEvent(new Event('submit', { cancelable: true }));
    } catch (e) {
      alert('加入失敗: ' + e.message);
    }
  }

  async function removeWatch(symbol) {
    if (!confirm('從 watchlist 移除 ' + symbol + '?')) return;
    try {
      const res = await fetch('/api/dashboard/watchlist/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      });
      if (!res.ok) throw new Error(res.statusText);
      await loadMyWatchlist();
    } catch (e) {
      alert('移除失敗: ' + e.message);
    }
  }

  // expose for inline handlers
  window.runDiscover = runDiscover;
  window.addWatch = addWatch;
  window.removeWatch = removeWatch;

  // init
  document.getElementById('footer-time').textContent = new Date().toLocaleString('zh-TW');
  loadMyWatchlist();
})();
