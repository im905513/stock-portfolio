# 更新指引：AI 投資輔助 API (`/api/ai/*`)

> 這份文件是給「升級測試機」用的 runbook。直接照順序執行即可，每一步都附驗證指令。

## 本次更動摘要

- **新檔**：`ai_routes.py`、`daily_snapshot.py`、`docs/UPDATE_2026-04-08_ai_api.md`（本檔）
- **改檔**：`main.py`（init_db 加 4 張表、新增 `setting_get/set` + `/api/goal/2031`、掛上 `ai_router`）
- **新表**：`nav_history`、`allocation_targets`、`alert_rules`、`thesis`（首次啟動 server 自動建立）
- **新環境變數**：`AI_API_TOKEN`（必填，否則 `/api/ai/*` 一律 500）
- **新對外 API**：`/api/ai/portfolio` `/api/ai/stock/{symbol}` `/api/ai/risk` `/api/ai/screen` `POST /api/ai/journal`
- **既有 API 不動**：所有 `/api/*`（非 `ai/`）行為保持原樣

---

## Step 1 — 拉最新 code

```bash
cd /home/ubuntu/stock-portfolio   # 或測試機實際路徑
git fetch origin
git checkout master
git pull origin master
```

驗證：
```bash
git log --oneline -3
# 應該看到: 71d5358 feat: AI 投資輔助 API namespace (/api/ai/*)
```

---

## Step 2 — 設定 `.env`

`.env` 在 `.gitignore` 內，需要手動建立 / 補欄位：

```bash
# 在 repo root
cat > .env <<'EOF'
ALPHA_VANTAGE_KEY=D96JDZGKMQTUFK0O
AI_API_TOKEN=請改成隨機字串_例如_openssl_rand_hex_24
EOF
chmod 600 .env
```

**重要**：`AI_API_TOKEN` 請真的換掉（範例 token 已 push 到 git，視為公開）。建議：
```bash
echo "AI_API_TOKEN=$(openssl rand -hex 24)" >> .env
```

驗證：
```bash
grep -c AI_API_TOKEN .env   # 應該回 1
```

---

## Step 3 — 重啟 server，自動建立新表 + 跑 migrations

`init_db()` 會 `CREATE TABLE IF NOT EXISTS` 建基礎表，`run_migrations()` 會按順序執行 `migrations/NNN_*.sql` 裡尚未套用的 migration（透過 `schema_migrations` 表追蹤），兩者**啟動時自動**，不用手動下 SQL。

目前 migrations 清單：
- `001_seed_thesis.sql` — 為 5 檔持股寫入初始 thesis（target / stop loss / 退出條件）

```bash
# 用你目前的啟動方式，例如：
sudo systemctl restart stock-portfolio
# 或
pkill -f "uvicorn main:app"
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 > /var/log/stock.log 2>&1 &
```

驗證新表都建好 + migration 都跑過：
```bash
sqlite3 stock.db ".tables"
# 應該包含: alert_rules allocation_targets cash nav_history positions
#          schema_migrations settings stock_prices stocks thesis trades

sqlite3 stock.db "SELECT * FROM schema_migrations"
# 應該至少看到: 001_seed_thesis|<timestamp>

sqlite3 stock.db "SELECT COUNT(*) FROM thesis WHERE status='active'"
# 應該 ≥ 5 (每檔持股至少一筆)
```

查 server log 應看到 `[migration] applied 001_seed_thesis.sql` 那一行（或空 — 若之前已套用過就不會再跑）。

---

## Step 4 — 跑一次 daily_snapshot.py（建立第一筆基準資料）

```bash
cd /home/ubuntu/stock-portfolio
python3 daily_snapshot.py
```

預期輸出（數字依持股而異）：
```
[2026-04-08] NAV=468,023  equity=122,016  cash=346,007  TWII=34642.78  SP500=6616.85  positions=5
```

驗證：
```bash
sqlite3 stock.db "SELECT * FROM nav_history ORDER BY date DESC LIMIT 3"
sqlite3 stock.db "SELECT date, stock_id, market_value, pnl_pct FROM positions ORDER BY date DESC LIMIT 5"
```

---

## Step 5 — 排程每日快照（cron）

決策：盤後 14:30 跑（台股收盤後）。如果你已經有 `update_prices.py` 的排程，就把 `daily_snapshot.py` 接在後面。

```bash
crontab -e
```

加入（或修改現有那行）：
```cron
30 14 * * 1-5 cd /home/ubuntu/stock-portfolio && /usr/bin/python3 update_prices.py >> /var/log/stock-update.log 2>&1 && /usr/bin/python3 daily_snapshot.py >> /var/log/stock-snapshot.log 2>&1
```

驗證：
```bash
crontab -l | grep daily_snapshot
```

---

## Step 6 — Smoke test 所有 `/api/ai/*` endpoint

把 `AI_API_TOKEN` 換成你 Step 2 設的值：

```bash
TOK="你的_AI_API_TOKEN"
HOST="http://localhost:8000"   # 或測試機對外 host

# 6.1 沒帶 token → 401
curl -s -o /dev/null -w "no_token: %{http_code}\n" $HOST/api/ai/portfolio
# 預期: no_token: 401

# 6.2 錯 token → 401
curl -s -o /dev/null -w "bad_token: %{http_code}\n" -H "Authorization: Bearer wrong" $HOST/api/ai/portfolio
# 預期: bad_token: 401

# 6.3 對的 token → 200，且結構完整
curl -s -H "Authorization: Bearer $TOK" $HOST/api/ai/portfolio | python3 -m json.tool | head -30
# 預期看到: snapshot_at / nav / positions / concentration / performance / drift / alerts / goal_2031

# 6.4 個股 context
curl -s -H "Authorization: Bearer $TOK" $HOST/api/ai/stock/2330 | python3 -m json.tool | head -50
# 預期看到: quote / holding / valuation (含 price_zscore) / institutional_5d / revenue_12m / thesis / alerts

# 6.5 風險（首日只會回 note，要等 2 天以上 nav_history）
curl -s -H "Authorization: Bearer $TOK" $HOST/api/ai/risk
# 第一天預期: {"data_points":1,"note":"需至少 2 天 nav_history 才能算風險指標"}

# 6.6 篩選器
curl -s -H "Authorization: Bearer $TOK" "$HOST/api/ai/screen?market=TW" | python3 -m json.tool

# 6.7 寫 thesis
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"symbol":"2330","thesis":"AI/HPC 龍頭","target_price":2300,"stop_loss":1700}' \
  $HOST/api/ai/journal
# 預期: {"id":1,"ok":true}
```

---

## Step 7 —（可選）建立 alert rules / allocation targets

這兩張表是空的，AI 需要你預先告訴它「什麼時候該警示」「目標配置長怎樣」。先給最少可用的範例：

```sql
sqlite3 stock.db <<'EOF'
-- 警示規則
INSERT INTO alert_rules (scope,target,metric,op,threshold,severity,message) VALUES
  ('portfolio','','top1_pct','>',15,'warn','單一持股權重超過 15%，集中度風險'),
  ('portfolio','','cash_pct','>',60,'info','現金比過高，可考慮加碼'),
  ('portfolio','','sector_pct_半導體','>',40,'warn','半導體權重 > 40%'),
  ('portfolio','','drawdown','<',-15,'critical','投組回撤超過 15%'),
  ('stock','2330','pe','>',35,'warn','2330 PE > 35 估值偏貴'),
  ('stock','2330','pnl_pct','>',30,'info','2330 報酬 > 30%, 可考慮停利');

-- 目標配置（依你規劃調整）
INSERT INTO allocation_targets (scope,key,target_pct,note) VALUES
  ('sector','半導體',30,'AI/HPC 主題'),
  ('sector','金融',20,'存股核心'),
  ('sector','食品',10,'防禦'),
  ('region','TW',70,'主場'),
  ('region','USD',30,'分散匯率風險');
EOF
```

驗證：
```bash
TOK="你的_AI_API_TOKEN"
curl -s -H "Authorization: Bearer $TOK" $HOST/api/ai/portfolio | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('alerts:', len(d['alerts']), '個')
for a in d['alerts']: print(' ', a['severity'], a['message'])
print('drift:', len(d['drift']), '個')
for x in d['drift']: print(' ', x['scope'], x['key'], x['current_pct'], 'vs', x['target_pct'], '→', x['action'])
"
```

---

## Step 8 — 把 token 給 AI

把這份資訊給你要用的 AI agent：

```
Base URL: http://你的host:8000
Auth header: Authorization: Bearer <AI_API_TOKEN>

Endpoints:
- GET /api/ai/portfolio          投組總覽 + 警示
- GET /api/ai/stock/{symbol}     單檔完整 context
- GET /api/ai/risk               風險指標
- GET /api/ai/screen?...         候選股篩選
- POST /api/ai/journal           寫投資論點
```

---

## Rollback（萬一壞了）

新表是 additive，不會破壞既有資料。要回退只要 checkout 上一版：

```bash
git checkout 0a743e4 -- main.py     # 上一版 main.py
rm ai_routes.py daily_snapshot.py   # 移除新檔
sudo systemctl restart stock-portfolio
```

新表 (`nav_history` 等) 留著無害；要清掉的話：
```sql
sqlite3 stock.db "DROP TABLE nav_history; DROP TABLE allocation_targets; DROP TABLE alert_rules; DROP TABLE thesis;"
```

---

## 已知限制 / 未做

- `forward_annual_estimate_twd`（被動收入估算）目前固定 0，要等之後把殖利率拉進來
- `risk` endpoint 第一天無法給數字（需累積 nav_history）
- alert_rules / allocation_targets / thesis 還沒 UI，目前只能 SQL 操作
- 沒有 MCP server 包裝（Phase 3，視需要再做）
