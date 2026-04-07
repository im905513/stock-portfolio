"""
Tim 股票投資系統 - FastAPI 後端
"""
import sqlite3, os, urllib.request, json
from datetime import datetime, date, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from contextlib import contextmanager

DATABASE = "/home/ubuntu/stock-portfolio/stock.db"
STATIC_DIR = "/home/ubuntu/stock-portfolio/static"

app = FastAPI(title="Tim Stock Portfolio")
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
        CREATE TABLE IF NOT EXISTS stock_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            price_date DATE NOT NULL,
            close_price REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'TWD',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_id, price_date)
        );
        """)

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

# ─── Stock CRUD ───────────────────────────────────────────

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

# ─── Trades ──────────────────────────────────────────────

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

# ─── Positions ───────────────────────────────────────────

@app.get("/api/positions")
def list_positions():
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
            positions.append({**dict(r), 'shares': shares, 'avg_cost': avg_cost, 'cost_basis': round(cost_basis, 2)})
        return positions

# ─── NAV ─────────────────────────────────────────────────

@app.get("/api/nav")
def get_nav():
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
            SELECT SUM(CASE WHEN action='deposit' THEN amount WHEN action IN ('withdraw','stock_purchase') THEN -amount WHEN action='stock_sell' THEN amount ELSE 0 END) AS cash FROM cash
        """).fetchone()
        cash = float(cash_row['cash']) if cash_row and cash_row['cash'] else 0
        return {"positions_count": len(positions), "total_cost": sum(float(r['cost_basis']) for r in positions), "cash": cash}

@app.get("/api/dashboard")
def dashboard():
    positions = list_positions()
    trades = list_trades()
    return {"positions": positions, "recent_trades": trades[:10], "total_cost": round(sum(p['cost_basis'] for p in positions), 2), "positions_count": len(positions)}

# ─── Cash ────────────────────────────────────────────────

@app.post("/api/cash")
def add_cash(c: CashIn):
    with get_db() as db:
        cur = db.execute("INSERT INTO cash (date,action,amount,note) VALUES (?,?,?,?)", (c.date, c.action, c.amount, c.note))
        db.commit()
        return {"id": cur.lastrowid}

@app.get("/api/cash")
def list_cash():
    with get_db() as db:
        rows = db.execute("SELECT * FROM cash ORDER BY date DESC").fetchall()
        return [dict(r) for r in rows]

# ─── Feature 4: Industry Allocation ──────────────────────

@app.get("/api/industry-allocation")
def industry_allocation():
    with get_db() as db:
        rows = db.execute("""
            SELECT s.sector,
                   SUM(CASE WHEN t.action='buy' THEN t.shares*t.price ELSE -t.shares*t.price END) AS cost,
                   COUNT(DISTINCT s.id) AS stock_count
            FROM stocks s JOIN trades t ON s.id=t.stock_id
            GROUP BY s.sector
            HAVING cost > 0
        """).fetchall()
        total = sum(float(r['cost']) for r in rows)
        return [{'sector': r['sector'] or '其他', 'cost': round(float(r['cost']), 0), 'stock_count': r['stock_count'],
                 'pct': round(float(r['cost'])/total*100, 1) if total > 0 else 0} for r in rows]

# ─── Feature 5: Fundamentals ─────────────────────────────

def fetch_finmind(dataset, symbol, start, end, token=""):
    url = f"https://api.finmindtrade.com/api/v4/data?dataset={dataset}&data_id={symbol}&start_date={start}&end_date={end}&token={token}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())

@app.get("/api/stocks/{stock_id}/valuation")
def get_valuation(stock_id: int):
    """P/E、P/B、殖利率、52週區間"""
    with get_db() as db:
        stock = db.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not stock:
            raise HTTPException(404, "找不到這檔股票")
        stock = dict(stock)

    today = date.today()
    start60 = (today - timedelta(days=60)).isoformat()
    end = today.isoformat()
    result = {'stock': stock, 'per': None, 'pbr': None, 'dividend_yield': None,
              'price_52w_high': None, 'price_52w_low': None, 'error': None}

    if stock['market'] != 'TW':
        result['error'] = '僅支援台股'
        return result

    try:
        data = fetch_finmind("TaiwanStockPER", stock['symbol'], start60, end)
        if data.get('data'):
            latest = data['data'][-1]
            result['per'] = latest.get('PER')
            result['pbr'] = latest.get('PBR')
            result['dividend_yield'] = latest.get('dividend_yield')
    except Exception as e:
        result['error'] = str(e)

    # 52週高低 - 用365天期間
    try:
        start365 = (today - timedelta(days=365)).isoformat()
        data = fetch_finmind("TaiwanStockPrice", stock['symbol'], start365, end)
        if data.get('data'):
            prices = [float(d['close']) for d in data['data'] if d.get('close')]
            if prices:
                result['price_52w_high'] = max(prices)
                result['price_52w_low'] = min(prices)
    except:
        pass

    # 即時收盤價
    try:
        data = fetch_finmind("TaiwanStockPrice", stock['symbol'], end, end)
        if data.get('data'):
            result['current_price'] = float(data['data'][-1]['close'])
    except:
        pass

    return result

@app.get("/api/stocks/{stock_id}/institutional")
def get_institutional(stock_id: int):
    """三大法人近5日買賣超（依日期分組）"""
    with get_db() as db:
        stock = db.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not stock:
            raise HTTPException(404, "找不到這檔股票")
        stock = dict(stock)

    if stock['market'] != 'TW':
        return {'stock': stock, 'data': None, 'error': '僅支援台股'}

    today = date.today()
    start = (today - timedelta(days=30)).isoformat()
    end = today.isoformat()

    try:
        data = fetch_finmind("TaiwanStockInstitutionalInvestorsBuySell", stock['symbol'], start, end)
        if not data.get('data'):
            return {'stock': stock, 'data': [], 'error': None}

        rows = data['data']
        # 依日期分組，彙總各單位
        by_date = {}
        for r in rows:
            d = r['date']
            buy = int(r.get('buy') or 0)
            sell = int(r.get('sell') or 0)
            net = buy - sell
            name = r.get('name', '')
            if d not in by_date:
                by_date[d] = {'date': d, 'foreign_net': 0, 'investment_trust_net': 0, 'dealer_net': 0}
            if 'Foreign' in name or 'foreign' in name:
                by_date[d]['foreign_net'] += net
            elif 'Investment_Trust' in name:
                by_date[d]['investment_trust_net'] += net
            elif 'Dealer' in name:
                by_date[d]['dealer_net'] += net

        sorted_dates = sorted(by_date.keys(), reverse=True)[:5]
        result = [by_date[d] for d in sorted_dates]
        return {'stock': stock, 'data': result, 'error': None}
    except Exception as e:
        return {'stock': stock, 'data': None, 'error': str(e)}

@app.get("/api/stocks/{stock_id}/revenue")
def get_revenue(stock_id: int):
    """月營收近12月"""
    with get_db() as db:
        stock = db.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not stock:
            raise HTTPException(404, "找不到這檔股票")
        stock = dict(stock)

    if stock['market'] != 'TW':
        return {'stock': stock, 'data': None, 'error': '僅支援台股'}

    today = date.today()
    start = (today - timedelta(days=400)).isoformat()
    end = today.isoformat()

    try:
        data = fetch_finmind("TaiwanStockMonthRevenue", stock['symbol'], start, end)
        if not data.get('data'):
            return {'stock': stock, 'data': [], 'error': None}

        rows = data['data'][-12:]
        result = []
        for i, r in enumerate(rows):
            rev = float(r.get('revenue') or 0)
            prev_rev = float(rows[i-1]['revenue']) if i > 0 and rows[i-1].get('revenue') else rev
            mom = round((rev - prev_rev) / prev_rev * 100, 1) if prev_rev > 0 else 0
            result.append({'date': r['date'], 'revenue': rev, 'mom': mom})
        return {'stock': stock, 'data': result, 'error': None}
    except Exception as e:
        return {'stock': stock, 'data': None, 'error': str(e)}

@app.get("/api/stocks/{stock_id}/ai-export")
def get_ai_export(stock_id: int):
    """整合所有資料，輸出 AI 分析摘要"""
    val = get_valuation(stock_id)
    inst = get_institutional(stock_id)
    rev = get_revenue(stock_id)

    with get_db() as db:
        stock = db.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not stock:
            raise HTTPException(404, "找不到這檔股票")
        stock = dict(stock)

        pos = db.execute("""
            SELECT s.symbol, s.name,
                   SUM(CASE WHEN t.action='buy' THEN t.shares ELSE -t.shares END) AS shares,
                   SUM(CASE WHEN t.action='buy' THEN t.shares*t.price ELSE 0 END) AS cost
            FROM stocks s JOIN trades t ON s.id=t.stock_id
            WHERE s.id=? GROUP BY s.id HAVING shares > 0
        """, (stock_id,)).fetchone()

    lines = []
    lines.append(f"股票：{stock['symbol']} {stock['name']}（{stock['sector'] or '其他'}）")
    lines.append(f"快照時間：{datetime.now().strftime('%Y-%m-%d %H:%M')} GMT+8")
    lines.append("")

    if val.get('per'):
        lines.append("【估值】")
        lines.append(f"- 本益比 P/E：{val['per']}（fair value 依產業均值自行判斷）")
        lines.append(f"- 股價淨值比 P/B：{val['pbr']}")
        lines.append(f"- 殖利率：{val['dividend_yield']}%")
        if val.get('current_price'):
            lines.append(f"- 現價：NT${val['current_price']}")
        if val.get('price_52w_high'):
            lines.append(f"- 52週區間：NT${val['price_52w_low']} ~ NT${val['price_52w_high']}")
        lines.append("")

    if inst.get('data'):
        lines.append("【法人動向（近5日）】")
        for r in inst['data']:
            f = r['foreign_net']; t = r['investment_trust_net']; d = r['dealer_net']
            lines.append(f"- {r['date']}  外資:{f:+,} 投信:{t:+,} 自營:{d:+,}")
        lines.append("")

    if rev.get('data'):
        lines.append("【月營收（近12月）】")
        for r in rev['data'][-6:]:
            rev_str = f"NT${r['revenue']/1e8:.2f}億"
            mom_str = f"MoM {r['mom']:+.1f}%"
            lines.append(f"- {r['date']}  {rev_str}  {mom_str}")
        lines.append("")

    if pos:
        shares = float(pos['shares'])
        cost = float(pos['cost'])
        avg = cost / shares if shares > 0 else 0
        lines.append("【持股資訊】")
        lines.append(f"- 持有：{shares} 股，均價 NT${avg:.2f}，成本 NT${cost:,.0f}")
        lines.append("")

    lines.append("【問我】")
    lines.append("請根據以上數據，評估：")
    lines.append("1. 現在估值是否合理？")
    lines.append("2. 法人動向對後市的暗示")
    lines.append("3. 月營收趨勢的隱憂或亮點")
    lines.append("4. 給持有者的具體建議")

    return {
        'stock': stock,
        'text': '\n'.join(lines),
        'valuation': val,
        'institutional': inst.get('data'),
        'revenue': rev.get('data'),
    }

# 舊的相容性 endpoint（向下相容）
@app.get("/api/fundamentals/{stock_id}")
def get_fundamentals(stock_id: int):
    val = get_valuation(stock_id)
    inst = get_institutional(stock_id)
    rev = get_revenue(stock_id)
    with get_db() as db:
        stock = db.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not stock:
            raise HTTPException(404, "找不到這檔股票")
    return {'stock': dict(stock), 'per': val.get('per'), 'pbr': val.get('pbr'),
            'dividend_yield': val.get('dividend_yield'), 'current_price': val.get('current_price'),
            'price_52w_high': val.get('price_52w_high'), 'price_52w_low': val.get('price_52w_low'),
            'institutional': inst.get('data'), 'revenue': rev.get('data'), 'error': None}

@app.get("/fundamentals")
def fundamentals_page():
    return FileResponse(f"{STATIC_DIR}/fundamentals.html")

# ─── Static pages ──────────────────────────────────────────

@app.get("/")
def root(): return FileResponse(f"{STATIC_DIR}/index.html")
@app.get("/dashboard")
def dashboard_page(): return FileResponse(f"{STATIC_DIR}/dashboard.html")
@app.get("/stocks")
def stocks_page(): return FileResponse(f"{STATIC_DIR}/stocks.html")
@app.get("/trades")
def trades_page(): return FileResponse(f"{STATIC_DIR}/trades.html")
@app.get("/positions")
def positions_page(): return FileResponse(f"{STATIC_DIR}/positions.html")

# ─── Init ─────────────────────────────────────────────────
init_db()
