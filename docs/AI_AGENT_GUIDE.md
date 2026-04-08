# AI Agent 操作指引 — Tim Stock Portfolio API

> 這份文件是寫給 AI agent（Claude / GPT / Gemini / MCP server）看的，讓你用 **最少呼叫次數** 拿到做投資決策需要的全部 context，並把結論寫回系統。

---

## 1. 連線設定

**Base URL**：使用者會告訴你，通常長這樣
- 本機開發：`http://localhost:8000` 或 `http://127.0.0.1:8000`
- 測試機內網：`http://192.168.x.x:8000`

**認證**：所有 `/api/ai/*` endpoint 需要 Bearer token
```
Authorization: Bearer <AI_API_TOKEN>
```
Token 由使用者提供，請**不要**在對話中回顯完整 token、也不要寫進檔案、不要猜測。拿到後暫存在當次對話記憶內即可。

**錯誤碼**：
- `401` — token 缺 / 錯 → 問使用者
- `404` — 股票沒註冊 → 先 `POST /api/stocks` 或告訴使用者
- `403` — 你從外網打 `/api/dashboard/*`（那些限本機）→ 改用 `/api/ai/*`
- `500` — 通常是 `AI_API_TOKEN` 伺服器端沒設定 → 告訴使用者

---

## 2. 核心端點速查

| Endpoint | 用途 | 最常用於 |
|---|---|---|
| `GET /api/ai/portfolio` | **投組全景** — nav / positions / 集中度 / 績效 / drift / alerts / 2031 進度 | 「幫我看整個投組現在狀況」 |
| `GET /api/ai/stock/{symbol}` | **單檔決策 context** — 即時報價 / 估值 / 法人 / 營收 / thesis / alerts | 「2330 現在能不能加碼？」 |
| `GET /api/ai/risk` | 風險指標 — 波動率 / drawdown / beta vs TWII | 「現在風險部位如何？」 |
| `GET /api/ai/screen?market=TW&max_pe=15&min_yield=5` | 篩選候選股 | 「找 PE < 15 殖利率 > 5% 的金融股」 |
| `POST /api/ai/journal` | 寫回投資論點 (thesis) | 結論確認後寫回「我們決定 X」 |
| `GET /api/goal/2031` | 2031 目標進度 + CAGR 推算 | 「我 2031 能達標嗎？」 |

**規則：能用一個 endpoint 解決就別分多次打**。`/api/ai/portfolio` 已經包含 alerts / drift / goal_2031，不用再分開查。

---

## 3. 常見決策工作流

### 3.1 「投組整體健康嗎？」

```bash
GET /api/ai/portfolio
```
單次呼叫拿到：
- `nav.total / nav.cash / nav.equity` — 總資產結構
- `concentration.top1_pct / top3_pct / hhi` — 集中度
- `concentration.by_sector` — 產業分布
- `drift[]` — 相對於 `allocation_targets` 的偏離
- `alerts[]` — **觸發中的警示**（severity: critical / warn / info）
- `goal_2031.gap / implied_cagr_required` — 距目標差多遠
- `performance.max_drawdown_pct` — 歷史最大回撤

**決策邏輯**：
1. **先看 `alerts` 長度**。若 `severity=critical` 的有任何一筆 → 那就是最高優先的事。
2. 看 `drift` 有沒有 `diff_pct > 10` 的項目 → 建議再平衡
3. 看 `goal_2031.implied_cagr_required`：> 0.3（30%）= 幾乎不可能，要嘛加碼定投、要嘛降低目標
4. 看 `concentration.top1_pct`：> 30% = 過度集中

### 3.2 「某檔能不能加碼 / 該不該出？」

```bash
GET /api/ai/stock/{symbol}
```
一次拿到：
- `quote.price / change_pct` — 即時
- `holding` — 目前持有多少、成本、未實現損益
- `valuation.per / pbr / dividend_yield / price_zscore` — 估值相對位置
  - **`price_zscore`**：相對於過去 1 年的價位 z 分數。>1.5 = 偏貴，<-1.5 = 偏便宜
- `institutional_5d` — 近 5 日外資 / 投信 / 自營商淨買賣
- `revenue_12m` — 近 12 月營收 + MoM
- `thesis[]` — **當初買的理由、目標價、停損價**（最重要！）
- `alerts[]` — 這檔觸發的個別 alert

**決策邏輯**：
1. **先看 `thesis`**，檢查「當初的理由還成立嗎」。若 thesis.exit_condition 被觸發 → 賣。
2. 看 `holding.unrealized_pnl_pct` 對照 `thesis.target_price / stop_loss`：
   - 接近 `target_price`（距目標 < 5%）→ 可考慮停利或出一半
   - 接近 `stop_loss`（距停損 < 5%）→ **紀律停損**，不要凹
3. 看 `valuation.price_zscore`：加碼只在 z < 0 時（相對便宜區）
4. 看 `institutional_5d` 外資是否連續買超 → 動能訊號

### 3.3 「找新標的」

```bash
GET /api/ai/screen?market=TW&style=dca&max_pe=15&min_yield=5
```
參數：
- `market` — TW / US
- `style` — dca（存股）/ thematic（題材）/ trade
- `sector` — 「金融」「半導體」等
- `max_pe` — 最大本益比
- `min_yield` — 最低殖利率 %
- `held_only` — true 只看已持有

回傳 `candidates[]`，已排序（未持有優先、PE 低優先）。

### 3.4 「分析完要把結論存下來」

```bash
POST /api/ai/journal
Content-Type: application/json

{
  "symbol": "2330",
  "thesis": "AI/HPC 龍頭，CoWoS 滿載到 2027",
  "exit_condition": "PE > 35 或 CoWoS 訂單減少",
  "target_price": 2300,
  "stop_loss": 1700
}
```

**什麼時候寫**：
- 使用者確認「這個分析寫回去」→ 寫
- 使用者只是問問 → **不要**寫
- 改變想法（thesis 推翻）→ 先呼叫（目前只支援 INSERT 新 row，舊的會留著。之後可能加 PATCH）

---

## 4. 回應結構重點欄位

### `/api/ai/portfolio` 關鍵欄位
```
{
  "nav": { "total", "cash", "equity" },
  "positions": [{ "symbol", "weight_pct", "pnl_pct", ... }],
  "concentration": { "top1_pct", "top3_pct", "hhi", "by_sector"[], "by_style", "by_currency" },
  "performance": { "since_inception_pct", "ytd_pct", "30d_pct", "vs_twii_30d_pct", "max_drawdown_pct" },
  "drift": [{ "scope", "key", "target_pct", "current_pct", "diff_pct", "action" }],
  "alerts": [{ "severity", "metric", "threshold", "actual", "message" }],
  "goal_2031": { "target_nav", "current_nav", "gap", "implied_cagr_required" }
}
```

### `/api/ai/stock/{symbol}` 關鍵欄位
```
{
  "quote": { "price", "change_pct", "prev_close" },
  "holding": { "shares", "avg_cost", "unrealized_pnl_pct" } or null,
  "valuation": { "per", "pbr", "dividend_yield", "price_52w_high", "price_52w_low", "price_zscore" },
  "institutional_5d": [{ "date", "foreign_net", "trust_net", "dealer_net" }],
  "revenue_12m": [{ "date", "revenue", "mom_pct" }],
  "thesis": [{ "thesis", "exit_condition", "target_price", "stop_loss" }],
  "alerts": [...]
}
```

---

## 5. 限制與注意事項

### 資料可得性
- **TW 股（4-6 碼數字）**：FinMind 提供完整基本面
- **US 股（字母符號）**：只有 Yahoo 即時報價，**無** PE / PBR / 殖利率 / 法人 / 月營收 — `valuation` 欄位會是 null
- **US ETF（GDX 等）**：同上，需靠技術面 + 題材判斷

### 時序資料
- **新安裝的系統**：`nav_history` 是空的或只有 1 筆 → `/api/ai/risk` 會回 `note: 需至少 2 天 nav_history`，`performance.since_inception_pct` 也會是 null
- **資料會每天累積**（`daily_snapshot.py` cron 跑）— 跑越久資料越完整
- 若使用者問「我的波動率」但系統才剛上線 → 誠實告訴他「至少需要 2 週資料才有意義」

### 快取
- 即時報價 TTL 30 秒（TWSE/Yahoo）
- Alpha Vantage 120 秒
- nav_history 60 秒
- 別擔心短時間內連打同一 endpoint，不會爆 API

### 警示規則是「被動」的
- `alerts[]` 只在你打 API 時才即時計算
- 系統**不會主動推播**，所以你每次回答投資問題都該先 `GET /api/ai/portfolio` 看有沒有 alert，再回答

### 你寫回的 thesis 會被紀錄
- `POST /api/ai/journal` 寫入後，下次查 `/api/ai/stock/{symbol}` 會把它帶出來
- 這讓「thesis 紀律檢查」能運作 — **請在寫 thesis 時認真寫 exit_condition**，不要隨便「看情況」

---

## 6. Prompt 範例（使用者給你這些問題時怎麼回）

### Q: 「幫我看看 2330 現在能不能加碼？」
1. `GET /api/ai/stock/2330`
2. 檢查 `thesis[].target_price`（若已接近 → 不加碼）
3. 檢查 `valuation.price_zscore`（> 1 → 偏貴，不加碼）
4. 檢查 `institutional_5d` 外資近 5 日動向
5. 檢查 `holding.unrealized_pnl_pct` 當前獲利
6. 給結論：「**不建議加碼 / 可小量加碼 / 建議加碼**，因為 X、Y、Z」

### Q: 「我整個投組健康嗎？」
1. `GET /api/ai/portfolio`
2. 逐項檢查 `alerts`, `drift`, `concentration.top1_pct`, `goal_2031`
3. 結構化回覆：
   - 觸發中的警示（若有）
   - 集中度評估
   - 2031 目標進度
   - 下一步建議（再平衡 / 加碼 / 持有）

### Q: 「現在有哪些警示？」
1. `GET /api/ai/portfolio`
2. 只列 `alerts[]`，按 severity 排序（critical → warn → info）
3. 每筆講清楚：是什麼 metric、當前值 vs 門檻值、建議動作

### Q: 「幫我找 PE 低、殖利率高的金融股」
1. `GET /api/ai/screen?sector=金融&max_pe=15&min_yield=4.5`
2. 排序結果給使用者
3. 對前 3 名各打 `GET /api/ai/stock/{symbol}` 拉詳細資料做比較表

### Q: 「2330 的分析結論寫下來吧」
1. 使用者明確同意後 → `POST /api/ai/journal` 帶你剛分析的結論
2. 確認 `{"ok": true}` 後回報使用者「已寫入」

---

## 7. 不要做的事

- ❌ 不要在**沒有使用者確認**的狀況下 `POST /api/ai/journal`
- ❌ 不要幻覺沒問過的股票代碼 — 先 `GET /api/ai/stock/{symbol}`，若 404 就問使用者要不要註冊
- ❌ 不要用**非投組股票** `/api/ai/portfolio` 的集中度數字來判斷個股 — 要查個股就打個股 endpoint
- ❌ 不要假設 `risk` 一定有數字 — 新系統前 2 週可能只有 `note`
- ❌ 不要用 `/api/ai/screen` 的結果**直接建議買入** — screen 只是篩選，真正買賣決策要再跑個股深度分析

---

## 8. 寫 thesis 的最佳實踐

AI 寫 thesis 時請遵守：

1. **exit_condition 必填**，且要**可量化**：
   - 好: `"PE > 35 或 CoWoS 訂單減少"`
   - 差: `"基本面轉差時出場"`

2. **target_price / stop_loss 都要有**，不要只給一邊

3. **stop_loss 要合理**：通常在成本下方 10-20%，不要設在成本之上（除非是保護獲利）

4. **thesis 文字**：用一句話講清「買的核心理由」，不要寫論文
   - 好: `"壽險轉型 + 外資強力加碼, PBR 仍低於同業均值"`
   - 差: `"覺得會漲"`

---

## 9. 擴充點（未來你可能會看到的新 endpoint）

這些還沒做，但當你看到時請當作「同樣的 AI namespace 風格」使用：
- `PATCH /api/ai/journal/{id}` — 編輯現有 thesis
- `POST /api/ai/alerts` — 新增 alert 規則
- `POST /api/ai/targets` — 設定 allocation targets
- `GET /api/ai/trade-suggest` — 再平衡交易建議
- MCP server wrapper — 讓你直接用 tool call 而不是 HTTP

若你看到這些已經存在但這份文件沒寫，就照語意推論使用方式。

---

**最後**：這是使用者的個人帳本，認真看待。每筆建議都可能影響真錢。不確定時請說「我不確定」，不要硬編故事。
