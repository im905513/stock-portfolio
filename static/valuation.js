// 價差評估頁 — 本益比 / 股淨比雙法分析
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]));

  let currentMethod = 'pe';   // 'pe' | 'pbr'

  function tagHtml(tag) {
    if (!tag) return '<span style="opacity:.4">--</span>';
    const label = { cheap: '<便宜', fair: '>合理', expensive: '>昂貴' }[tag] || tag;
    return `<span class="tag-${tag}">${label}</span>`;
  }

  function fmtNum(v, d = 2) {
    return v == null ? '--' : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });
  }

  function priceTriple(cheap, fair, expensive) {
    return `<div class="price-triple price-col">
      <div class="price-cheap">便 ${fmtNum(cheap)}</div>
      <div class="price-fair">合 ${fmtNum(fair)}</div>
      <div class="price-expensive">貴 ${fmtNum(expensive)}</div>
    </div>`;
  }

  function discountPct(current, fair) {
    if (current == null || fair == null || fair === 0) return '--';
    const pct = ((current / fair - 1) * 100).toFixed(1);
    const cls = pct < 0 ? 'discount-positive' : 'discount-negative';
    return `<span class="${cls}">${pct > 0 ? '+' : ''}${pct}%</span>`;
  }

  function renderTable(items, method) {
    if (!items || items.length === 0) {
      return '<div class="empty-state">目前沒有資料，點「↻ 重算」觸發</div>';
    }
    const isPe = method === 'pe';
    const rows = items.map((v) => {
      const cheap = isPe ? v.cheap_price : v.pbr_cheap_price;
      const fair = isPe ? v.fair_price : v.pbr_fair_price;
      const exp = isPe ? v.expensive_price : v.pbr_expensive_price;
      const tag = isPe ? v.tag : v.pbr_tag;
      const ratio = isPe
        ? `PE ${fmtNum(v.pe_low)}/${fmtNum(v.pe_mid)}/${fmtNum(v.pe_high)}`
        : `PBR ${fmtNum(v.pbr_low)}/${fmtNum(v.pbr_mid)}/${fmtNum(v.pbr_high)}`;
      const anchor = isPe
        ? (v.eps_used != null ? `預估EPS ${fmtNum(v.eps_used)}` : '--')
        : (v.bps != null ? `BPS ${fmtNum(v.bps)}` : '--');
      const growth = v.eps_growth_ytd != null ? `${(v.eps_growth_ytd * 100).toFixed(1)}%` : '--';
      return `
        <tr onclick="window.location='/fundamentals?stock_id=${v.stock_id}'">
          <td class="sym-cell">${esc(v.symbol)}<small>${esc(v.name || '')}</small></td>
          <td class="price-col">${fmtNum(v.current_price)}</td>
          <td>${priceTriple(cheap, fair, exp)}</td>
          <td>${tagHtml(tag)}</td>
          <td>${discountPct(v.current_price, fair)}</td>
          <td style="font-size:11px;opacity:.75">${ratio}</td>
          <td style="font-size:11px">${anchor}${isPe ? '<br><span style="opacity:.6">EPS成長 ' + growth + '</span>' : ''}</td>
          <td style="font-size:11px;opacity:.7">${esc(v.sector || v.category || '--')}</td>
        </tr>
      `;
    }).join('');
    return `
      <table class="val-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>現價</th>
            <th>便宜 / 合理 / 昂貴</th>
            <th>評估</th>
            <th>折價率</th>
            <th>${isPe ? 'PE 區間' : 'PBR 區間'}</th>
            <th>${isPe ? '預估 EPS' : 'BPS'}</th>
            <th>產業</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  async function load() {
    $('val-list').innerHTML = '<div class="empty-state">LOADING...</div>';
    $('val-meta').textContent = '載入中...';
    try {
      const r = await fetch('/api/valuation/list?sort=discount&limit=500');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      window._valItems = data.items;
      $('val-list').innerHTML = renderTable(data.items, currentMethod);
      $('val-meta').textContent = `${data.count} 檔`;
    } catch (e) {
      $('val-list').innerHTML = `<div class="empty-state" style="color:#ef4444">載入失敗：${esc(e.message)}</div>`;
      $('val-meta').textContent = 'ERROR';
    }
  }

  function switchMethod(method) {
    currentMethod = method;
    document.querySelectorAll('.tab-btn[data-method]').forEach((b) => {
      b.classList.toggle('active', b.dataset.method === method);
    });
    if (window._valItems) {
      $('val-list').innerHTML = renderTable(window._valItems, method);
    }
  }

  async function refresh() {
    const btn = $('refresh-btn');
    btn.disabled = true;
    btn.textContent = '計算中...';
    try {
      const r = await fetch('/api/valuation/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await r.json();
      alert(`重算完成：成功 ${data.ok}，失敗 ${data.failed}，總計 ${data.total}`);
      await load();
    } catch (e) {
      alert('重算失敗：' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '↻ 重算';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tab-btn[data-method]').forEach((btn) => {
      btn.addEventListener('click', () => switchMethod(btn.dataset.method));
    });
    $('refresh-btn').addEventListener('click', refresh);
    load();
  });
})();
