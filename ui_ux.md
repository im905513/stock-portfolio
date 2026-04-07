# MiniMax 修改指令 — fundamentals.html 視覺修正

以下是針對 `static/fundamentals.html` 的精確修改指令。
CSS（terminal.css）不需要動，所有 class 已存在，只需要修正 HTML 裡的 JS 渲染邏輯。

---

## 問題 1：Industry Allocation — bar 用文字字元而非 CSS div

**位置**：`loadAllocation()` function 內

**現在的錯誤寫法**：
```javascript
const bar = '█'.repeat(Math.round(w/2)) + '░'.repeat(50 - Math.round(w/2));
barsHtml += `<div class="alloc-row">
  <span class="alloc-sector" style="color:${color}">${r.sector}</span>
  ...
```

**改成**（移除 `bar` 變數，`.alloc-bar-wrap` 改用 CSS div）：
```javascript
barsHtml += `<div class="alloc-row">
  <span class="alloc-sector" style="color:${color}">${r.sector}</span>
  <div class="alloc-bar-wrap">
    <div class="alloc-bar-fill" style="width:${w}%;background:${color}"></div>
  </div>
  <span class="alloc-pct">${r.pct}%</span>
  <span class="alloc-cost">NT$${r.cost.toLocaleString()}</span>
  <span class="alloc-count">${r.stock_count}檔</span>
</div>`;
```

---

## 問題 2：Valuation — 數字字體太小，沒用到 font-display

**位置**：`loadValuation()` 裡 innerHTML 那段

**現在的寫法**：
```html
<span class="vc-val accent">${per}</span>
```

`.vc-val` 的 CSS 已有 `font-size: 16px; font-family: var(--font-display)`，但數字看起來仍小。
請在 HTML 的 `<style>` 區塊內（或在 terminal.css 的 `/* ─── Fundamentals v2 ─── */` 段落後面）加入：

```css
.vc-val {
  font-size: 22px !important;
  font-weight: 700 !important;
  font-family: var(--font-display) !important;
  letter-spacing: 0.02em;
}
```

---

## 問題 3：Institutional table — bar-wrap 寬度太窄看不清楚

**位置**：terminal.css 的 `.bar-wrap`

**現在**：
```css
.bar-wrap { display: inline-block; width: 40px; height: 3px; ... }
```

**改成**：
```css
.bar-wrap { display: inline-block; width: 60px; height: 5px; background: var(--border); margin-left: 6px; vertical-align: middle; border-radius: 2px; }
.bar-fill { display: block; height: 100%; border-radius: 2px; }
```

---

## 問題 4：法人買超 — 正值加綠色 + 號，負值加紅色

**位置**：`loadInst()` 的 `fmt` function

**現在**：
```javascript
const fmt = n => n > 0 ? `<span class="green">+${n.toLocaleString()}</span>` : ...
```

這個邏輯是對的，但 `.green` 和 `.red` class 在 inst-table 裡要確認有繼承顏色。
在 terminal.css 加入：

```css
.inst-table .green { color: var(--green) !important; }
.inst-table .red   { color: var(--red)   !important; }
```

---

## 問題 5：月營收趨勢 — Sparkline 太小

**位置**：`buildSparkline()` function

**現在**：
```javascript
const w = 120, h = 50, pad = 4;
```

**改成**：
```javascript
const w = 160, h = 70, pad = 6;
```

同時在 terminal.css 的 `.sparkline-wrap` 改成：
```css
.sparkline-wrap { display: flex; align-items: center; padding: 0.5rem; min-width: 170px; }
```

---

## 問題 6：AI Panel — 按鈕改成金色填充樣式（更醒目）

**位置**：terminal.css 的 `.btn`

**現在**：
```css
.btn {
  background: var(--bg-deeper);
  border: 1px solid var(--border-bright);
  color: var(--text-primary);
  ...
}
```

**改成**：
```css
.btn {
  background: rgba(245, 204, 48, 0.12);
  border: 1px solid var(--accent);
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 0.5rem 1rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  transition: all 0.15s;
  letter-spacing: 0.05em;
}
.btn:hover {
  background: rgba(245, 204, 48, 0.25);
  box-shadow: 0 0 10px rgba(245, 204, 48, 0.2);
}
```

---

## 問題 7：Header bar — 加入股價漲跌幅顏色

**位置**：`loadValuation()` 裡設定 `stock-price` 的地方

在設定 `document.getElementById('stock-price').textContent = cur;` 之後，
**加入**這段根據漲跌改色的邏輯：

```javascript
const priceEl = document.getElementById('stock-price');
priceEl.textContent = cur;
// 如果有當日漲跌資料
if (d.change_pct !== undefined) {
  priceEl.style.color = d.change_pct >= 0 ? 'var(--green)' : 'var(--red)';
  const sign = d.change_pct >= 0 ? '+' : '';
  priceEl.textContent = `${cur}  ${sign}${d.change_pct.toFixed(2)}%`;
} else {
  priceEl.style.color = 'var(--accent-bright)';
}
```

---

## 總結：改動清單

| 檔案 | 位置 | 改動 |
|------|------|------|
| fundamentals.html | `loadAllocation()` | 移除文字 bar，改用 CSS div |
| terminal.css | `.vc-val` | font-size 22px |
| terminal.css | `.bar-wrap` / `.bar-fill` | 加寬加高加 border-radius |
| terminal.css | `.inst-table .green/.red` | 新增 !important 色彩 |
| fundamentals.html | `buildSparkline()` | w=160, h=70 |
| terminal.css | `.sparkline-wrap` | min-width: 170px |
| terminal.css | `.btn` / `.btn:hover` | 金色填充樣式 |
| fundamentals.html | `loadValuation()` | 加漲跌幅顏色邏輯 |

**注意：不要動 main.py、不要動其他頁面的 HTML。只改上面列出的部分。**
