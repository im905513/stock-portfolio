"""
Tim 股票投資系統 - FastAPI 後端
"""
import sqlite3, os
from datetime import datetime, date
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from contextlib import contextmanager

DATABASE = "/home/ubuntu/stock-portfolio/stock.db"
STATIC_DIR = "/home/ubuntu/stock-portfolio/static"

app = FastAPI(title="Tim Stock Portfolio")

# Serve /static/* as files (terminal.css, terminal.js)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ─── DB helpers ───────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as db:
        db.executescript("""
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
        db.commit()

# ─── Pydantic models ──────────────────────────────────────

class StockIn(BaseModel):
    symbol: str
    name: str
    market: str
    sector: Optional[str] = None
    currency: str = "TWD"

class TradeIn(BaseModel):
    stock_id: int
    trade_date: str
    action: str
    shares: float
    price: float
    fee: float = 0
    currency: str = "TWD"
    note: Optional[str] = None

class CashIn(BaseModel):
    date: str
    action: str
    amount: float
    note: Optional[str] = None

# ─── Routes ────────────────────────────────────────────────

@app.get("/api/stocks")
def list_stocks():
    with get_db() as db:
        rows = db.execute("SELECT * FROM stocks ORDER BY symbol").fetchall()
        return [dict(r) for r in rows]

@app.post("/api/stocks")
def create_stock(s: StockIn):
    with get_db() as db:
        try:
            cur = db.execute(
                "INSERT INTO stocks (symbol,name,market,sector,currency) VALUES (?,?,?,?,?)",
                (s.symbol, s.name, s.market, s.sector, s.currency)
            )
            db.commit()
            return {"id": cur.lastrowid, **s.model_dump()}
        except sqlite3.IntegrityError:
            raise HTTPException(400, "股票代碼已存在")

@app.get("/api/stocks/{stock_id}")
def get_stock(stock_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "找不到")
        return dict(row)

@app.get("/api/trades")
def list_trades(
    stock_id: Optional[int] = None,
    action: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    q = "SELECT t.*, s.symbol, s.name FROM trades t JOIN stocks s ON t.stock_id=s.id WHERE 1=1"
    params = []
    if stock_id:
        q += " AND t.stock_id=?"
        params.append(stock_id)
    if action:
        q += " AND t.action=?"
        params.append(action)
    if from_date:
        q += " AND t.trade_date >= ?"
        params.append(from_date)
    if to_date:
        q += " AND t.trade_date <= ?"
        params.append(to_date)
    q += " ORDER BY t.trade_date DESC"
    with get_db() as db:
        rows = db.execute(q, params).fetchall()
        return [dict(r) for r in rows]

@app.post("/api/trades")
def create_trade(t: TradeIn):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO trades (stock_id,trade_date,action,shares,price,fee,currency,note) VALUES (?,?,?,?,?,?,?,?)",
            (t.stock_id, t.trade_date, t.action, t.shares, t.price, t.fee, t.currency, t.note)
        )
        db.commit()
        return {"id": cur.lastrowid, **t.model_dump()}

@app.get("/api/positions")
def list_positions():
    """目前持股均價計算"""
    with get_db() as db:
        rows = db.execute("""
            SELECT s.id, s.symbol, s.name, s.market, s.currency,
                   COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares WHEN t.action='sell' THEN -t.shares ELSE 0 END),0) AS shares,
                   COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares*t.price WHEN t.action='sell' THEN -t.shares*t.price ELSE 0 END),0) AS cost_basis,
                   s.sector
            FROM stocks s
            LEFT JOIN trades t ON s.id=t.stock_id
            GROUP BY s.id
            HAVING shares > 0
            ORDER BY s.symbol
        """).fetchall()

        positions = []
        for r in rows:
            shares = float(r['shares'])
            cost_basis = float(r['cost_basis'])
            avg_cost = round(cost_basis / shares, 2) if shares > 0 else 0
            positions.append({
                **dict(r),
                'shares': shares,
                'avg_cost': avg_cost,
                'cost_basis': round(cost_basis, 2)
            })
        return positions

@app.get("/api/nav")
def get_nav():
    """NAV 總資產"""
    with get_db() as db:
        positions = db.execute("""
            SELECT s.id, s.symbol, s.name, s.currency, s.market,
                   COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares WHEN t.action='sell' THEN -t.shares ELSE 0 END),0) AS shares,
                   COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares*t.price WHEN t.action='sell' THEN -t.shares*t.price ELSE 0 END),0) AS cost_basis
            FROM stocks s
            LEFT JOIN trades t ON s.id=t.stock_id
            GROUP BY s.id
            HAVING shares > 0
        """).fetchall()

        cash_row = db.execute("""
            SELECT SUM(CASE WHEN action='deposit' THEN amount WHEN action IN ('withdraw','stock_purchase') THEN -amount WHEN action='stock_sell' THEN amount ELSE 0 END) AS cash
            FROM cash
        """).fetchone()

        # Also add cash from initial holdings (deposits when first bought)
        cash = float(cash_row['cash']) if cash_row and cash_row['cash'] else 0

        return {
            "positions_count": len(positions),
            "total_cost": sum(float(r['cost_basis']) for r in positions),
            "cash": cash,
        }

@app.get("/api/dashboard")
def dashboard():
    positions = list_positions()
    trades = list_trades()
    recent_trades = trades[:10]

    total_cost = sum(p['cost_basis'] for p in positions)
    total_shares_cost = sum(p['cost_basis'] for p in positions)

    return {
        "positions": positions,
        "recent_trades": recent_trades,
        "total_cost": round(total_cost, 2),
        "positions_count": len(positions),
    }

@app.post("/api/cash")
def add_cash(c: CashIn):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO cash (date,action,amount,note) VALUES (?,?,?,?)",
            (c.date, c.action, c.amount, c.note)
        )
        db.commit()
        return {"id": cur.lastrowid}

@app.get("/api/cash")
def list_cash():
    with get_db() as db:
        rows = db.execute("SELECT * FROM cash ORDER BY date DESC").fetchall()
        return [dict(r) for r in rows]

# ─── Static pages ──────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse(f"{STATIC_DIR}/index.html")

@app.get("/dashboard")
def dashboard_page():
    return FileResponse(f"{STATIC_DIR}/dashboard.html")

@app.get("/stocks")
def stocks_page():
    return FileResponse(f"{STATIC_DIR}/stocks.html")

@app.get("/trades")
def trades_page():
    return FileResponse(f"{STATIC_DIR}/trades.html")

@app.get("/positions")
def positions_page():
    return FileResponse(f"{STATIC_DIR}/positions.html")

# ─── Init ─────────────────────────────────────────────────

init_db()
