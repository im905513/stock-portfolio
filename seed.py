#!/usr/bin/env python3
"""Initialize Tim's portfolio data into stock.db"""
import sqlite3

DATABASE = "/home/ubuntu/stock-portfolio/stock.db"

def seed():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    # Create tables
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS stocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        market TEXT NOT NULL CHECK(market IN ('TW','US')),
        sector TEXT,
        currency TEXT NOT NULL DEFAULT 'TWD',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER NOT NULL REFERENCES stocks(id),
        trade_date DATE NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('buy','sell','dividend')),
        shares REAL NOT NULL,
        price REAL NOT NULL,
        fee REAL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'TWD',
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER NOT NULL REFERENCES stocks(id),
        date DATE NOT NULL,
        shares REAL NOT NULL,
        avg_cost REAL NOT NULL,
        market_value REAL NOT NULL,
        pnl REAL NOT NULL,
        pnl_pct REAL NOT NULL,
        UNIQUE(stock_id, date)
    );
    CREATE TABLE IF NOT EXISTS cash (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE NOT NULL,
        action TEXT NOT NULL,
        amount REAL NOT NULL,
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    # Insert stocks
    stocks = [
        (2330, '2330', '台積電', 'TW', '半導體', 'TWD'),
        (2891, '2891', '中信金', 'TW', '金融', 'TWD'),
        (2883, '2883', '凱基金', 'TW', '金融', 'TWD'),
        (1229, '1229', '聯華', 'TW', '食品', 'TWD'),
        (9999, 'GDX', 'VanEck Gold Miners ETF', 'US', '原物料', 'USD'),
    ]
    for s in stocks:
        cur.execute("""
            INSERT OR IGNORE INTO stocks (id, symbol, name, market, sector, currency)
            VALUES (?, ?, ?, ?, ?, ?)
        """, s)

    # Insert trades
    trades = [
        (2330, '2026-03-01', 'buy', 22, 1820.0, 0, 'TWD', 'Initial position'),
        (2891, '2026-03-01', 'buy', 295, 50.7, 0, 'TWD', 'Initial position'),
        (2891, '2026-04-07', 'buy', 216, 55.8, 0, 'TWD', 'Monthly DCA'),
        (2883, '2026-03-01', 'buy', 20, 19.45, 0, 'TWD', 'Initial position'),
        (2883, '2026-04-07', 'buy', 585, 20.55, 0, 'TWD', 'Monthly DCA'),
        (1229, '2026-03-01', 'buy', 500, 44.3, 0, 'TWD', 'Initial position'),
        (9999, '2026-03-26', 'buy', 5, 84.43, 0, 'USD', 'Iran escalation play'),
    ]
    for t in trades:
        cur.execute("""
            INSERT INTO trades (stock_id, trade_date, action, shares, price, fee, currency, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, t)

    # Initial cash
    cur.execute("""
        INSERT OR IGNORE INTO cash (date, action, amount, note)
        VALUES (?, ?, ?, ?)
    """, ('2026-03-01', 'deposit', 420000, 'Initial capital from Tim'))

    conn.commit()

    # Verify positions
    print("=== Stocks ===")
    for row in cur.execute("SELECT id, symbol, name, market FROM stocks ORDER BY symbol").fetchall():
        print(f"  {row}")

    print("\n=== Positions (computed avg cost) ===")
    for row in cur.execute("""
        SELECT s.symbol, s.name, s.currency,
               COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares WHEN t.action='sell' THEN -t.shares ELSE 0 END),0) AS shares,
               COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares*t.price WHEN t.action='sell' THEN -t.shares*t.price ELSE 0 END),0) AS cost_basis
        FROM stocks s
        LEFT JOIN trades t ON s.id=t.stock_id
        GROUP BY s.id
        HAVING shares > 0
    """).fetchall():
        shares = float(row[3])
        cost = float(row[4])
        avg = round(cost/shares, 2) if shares > 0 else 0
        cur_symbol = row[0]
        currency = row[2]
        cost_str = f"{currency} {cost:,.2f}"
        print(f"  {cur_symbol} {row[1]}: {shares}股, 均價={currency} {avg}, 總成本={cost_str}")

    conn.close()
    print("\n✅ Seed complete — stock.db ready")

if __name__ == "__main__":
    seed()
