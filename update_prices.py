#!/usr/bin/env python3
"""
Tim 股票系統 — 盤後股價更新腳本
抓取收盤價 → 存入 stock_prices 表 → 吐出 NAV 摘要
"""
import sqlite3, urllib.request, json, sys
from datetime import date, datetime

DATABASE = "/home/ubuntu/stock-portfolio/stock.db"
FINMIND_TOKEN = ""  # 留空用 public token
TaiwanStocks = ["2330","2883","2891","1229"]  # 聯電、凱基、中信、聯華
USStocks = ["GDX"]  # 金礦 ETF

def get_finmind_price(symbol, token=""):
    """用 FinMind 取台股收盤價"""
    url = (
        f"https://api.finmindtrade.com/api/v4/data"
        f"?dataset=TaiwanStockPrice"
        f"&data_id={symbol}"
        f"&start_date={date.today()}"
        f"&end_date={date.today()}"
        f"&token={token}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
            if data.get("data"):
                return float(data["data"][-1]["close"])
    except Exception as e:
        print(f"FinMind error {symbol}: {e}", file=sys.stderr)
    return None

def get_usd_to_twd():
    """抓 USD/TWD 匯率（用 FinMind）"""
    url = (
        "https://api.finmindtrade.com/api/v4/data"
        "?dataset=ExchangeRate"
        "&data_id=USDTWD"
        f"&start_date={date.today()}"
        f"&end_date={date.today()}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
            if data.get("data"):
                return float(data["data"][-1]["close"])
    except:
        pass
    return 30.0  # 預設

def get_yahoo_price(symbol):
    """用 Yahoo Finance 取美股/ETF 收盤價"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            result = data["chart"]["result"][0]
            price = result["meta"]["regularMarketPrice"]
            return float(price)
    except Exception as e:
        print(f"Yahoo error {symbol}: {e}", file=sys.stderr)
    return None

def init_prices_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS stock_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            price_date DATE NOT NULL,
            close_price REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'TWD',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_id, price_date)
        )
    """)

def main():
    today = date.today()
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    init_prices_table(conn)

    usd_twd = get_usd_to_twd()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] USD/TWD: {usd_twd:.2f}")

    # 台股
    tw_prices = {}
    for sym in TaiwanStocks:
        p = get_finmind_price(sym, FINMIND_TOKEN)
        if p:
            tw_prices[sym] = p
            print(f"  {sym}: NT${p:.2f}")

    # 美股 (GDX)
    us_prices = {}
    for sym in USStocks:
        p = get_yahoo_price(sym)
        if p:
            us_prices[sym] = p
            print(f"  {sym}: US${p:.2f}")

    # 寫入 DB
    cur = conn.cursor()
    for sym, price in tw_prices.items():
        cur.execute(
            "INSERT OR REPLACE INTO stock_prices (stock_id, price_date, close_price, currency) VALUES ((SELECT id FROM stocks WHERE symbol=?), ?, ?, 'TWD')",
            (sym, today, price)
        )
    for sym, price in us_prices.items():
        cur.execute(
            "INSERT OR REPLACE INTO stock_prices (stock_id, price_date, close_price, currency) VALUES ((SELECT id FROM stocks WHERE symbol=?), ?, ?, 'USD')",
            (sym, today, price)
        )
    conn.commit()

    # 計算 NAV
    cur.execute("SELECT SUM(CASE WHEN action='deposit' THEN amount WHEN action IN ('withdraw','stock_purchase') THEN -amount WHEN action='stock_sell' THEN amount ELSE 0 END) FROM cash")
    cash = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT s.symbol, s.currency,
               SUM(CASE WHEN t.action='buy' THEN t.shares ELSE -t.shares END) as shares,
               SUM(CASE WHEN t.action='buy' THEN t.shares*t.price ELSE 0 END) as cost
        FROM trades t JOIN stocks s ON t.stock_id=s.id
        GROUP BY s.id
        HAVING shares > 0
    """)
    positions = cur.fetchall()

    total_nav = cash
    print(f"\n=== NAV 摘要 ({today}) ===")
    print(f"  現金: NT${cash:,.0f}")
    for p in positions:
        sym, currency, shares, cost = p["symbol"], p["currency"], p["shares"], p["cost"]
        if currency == "USD":
            cur.execute("SELECT close_price FROM stock_prices WHERE stock_id=(SELECT id FROM stocks WHERE symbol=?) AND price_date=?", (sym, today))
            row = cur.fetchone()
            price = row["close_price"] if row else None
            if price:
                mkt_val = shares * price * usd_twd
                total_nav += mkt_val
                print(f"  {sym}: {shares}股 @ US${price:.2f} = NT${mkt_val:,.0f} (cost NT${cost:,.0f})")
        else:
            cur.execute("SELECT close_price FROM stock_prices WHERE stock_id=(SELECT id FROM stocks WHERE symbol=?) AND price_date=?", (sym, today))
            row = cur.fetchone()
            price = row["close_price"] if row else None
            if price:
                mkt_val = shares * price
                total_nav += mkt_val
                print(f"  {sym}: {shares}股 @ NT${price:.2f} = NT${mkt_val:,.0f} (cost NT${cost:,.0f})")

    print(f"\n  總 NAV: NT${total_nav:,.0f}")
    conn.close()

if __name__ == "__main__":
    main()
