# Goldman Terminal — Tim's Portfolio Command Center

Bloomberg Terminal 風格的投資組合管理系統，部署於本地伺服器。

![Terminal Theme](https://img.shields.io/badge/Theme-Bloomberg%20Terminal-dark?style=for-the-badge)

## 🚀 即時訪問

```
http://192.168.88.174:8000
```

## 📌 主要功能

| 頁面 | 路徑 | 說明 |
|---|---|---|
| 首頁 | `/` | Mission Objective + NAV + Intel Feed |
| 儀表板 | `/dashboard` | NAV 總覽、進度、持股、近期交易 |
| 持股 | `/positions` | 完整持股列表 + 均價 + 浮動盈虧 |
| 交易 | `/trades` | 新增交易、篩選、歷史紀錄 |
| 股票 | `/stocks` | 股票註冊與管理 |

## 🎨 設計特色

- **Bloomberg Terminal 美學**：深色底 `#080910` + 橘黃強調 `#f0c040`
- **等寬字體**：JetBrains Mono + Rajdhani
- **零 Emoji**：全線條 Lucide Icons
- **動態 Ticker Bar**：TWII / S&P 500 / GDX / 油價
- **即時 LIVE 指示燈**

## 🛠 技術架構

```
Frontend : HTML + CSS + Vanilla JS（無框架）
Backend  : Python FastAPI
Database : SQLite (stock.db)
Server   : Uvicorn on 192.168.88.174:8000
Data     : FinMind API (台股) + Yahoo Finance (美股)
```

## 📊 資料庫結構

| Table | 用途 |
|---|---|
| `stocks` | 股票基本資料（代碼、名稱、市場）|
| `trades` | 買賣歷史（日期、數量、價格、成本）|
| `cash` | 現金流記錄（存款、提領、調整）|
| `stock_prices` | 收盤價（盤後自動更新）|

## ⏰ 自動任務

**每日 14:35（週一至五）** — 盤後股價更新

```bash
# 手動執行
python3 update_prices.py

# 自動執行（cron）
./run_price_update.sh
```

更新內容：
- 台股（2330、2883、2891、1229）收盤價
- GDX (VanEck Gold Miners ETF) 收盤價
- USD/TWD 匯率
- NAV 摘要計算

## 🚀 更新部署流程

```bash
# 1. 在本機修改程式碼
# 2. Commit
git add .
git commit -m "描述"
git push

# 3. 在 server 上更新
ssh ubuntu@192.168.88.174
cd ~/stock-portfolio
git pull
```

## 📁 目錄結構

```
stock-portfolio/
├── main.py              # FastAPI 後端
├── update_prices.py     # 盤後自動報價腳本
├── run_price_update.sh  # cron 執行殼
├── seed.py              # 初始資料
├── stock.db             # SQLite 資料庫
└── static/
    ├── index.html       # 首頁
    ├── dashboard.html   # 儀表板
    ├── positions.html   # 持股
    ├── trades.html      # 交易
    ├── stocks.html      # 股票管理
    ├── terminal.css     # Bloomberg 主題樣式
    └── terminal.js      # 前端邏輯
```

## 🔧 注意事項

- **`sqlite3` CLI 未安裝**在 server（用 Python 替代）
- CSS 更新後需 `Ctrl+Shift+R` 強刷或開無痕視窗
- GDX 為美股，NAV 計算使用 USD/TWD 匯率
- NAV 顯示含浮動盈虧，實際收益以賣出為準

## 📝 Git

```
Remote : git@github.com:im905513/stock-portfolio.git
Branch : master
```
