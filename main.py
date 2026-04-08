"""
Tim 股票投資系統 - FastAPI 後端
"""
import sqlite3, os, urllib.request, json
from datetime import datetime, date, timedelta
from typing import Optional, Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from contextlib import contextmanager

# ─── Load .env (no extra dependency) ─────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception as e:
        print(f"[env] load error: {e}")
_load_env()

_BASE = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.getenv("DB_PATH", os.path.join(_BASE, "stock.db"))
STATIC_DIR = os.getenv("STATIC_DIR", os.path.join(_BASE, "static"))
ALPHA_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")

app = FastAPI(title="Tim Stock Portfolio")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.middleware("http")
async def no_cache_middleware(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/dashboard", "/positions", "/trades", "/stocks", "/fundamentals"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

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
        CREATE TABLE IF NOT EXISTS nav_history (
            date DATE PRIMARY KEY,
            total_value REAL NOT NULL,
            cash REAL NOT NULL,
            equity_value REAL NOT NULL,
            twii_close REAL,
            sp500_close REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS allocation_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL CHECK(scope IN ('sector','stock','style','region')),
            key TEXT NOT NULL,
            target_pct REAL NOT NULL,
            note TEXT,
            UNIQUE(scope, key)
        );
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL CHECK(scope IN ('stock','portfolio')),
            target TEXT,
            metric TEXT NOT NULL,
            op TEXT NOT NULL CHECK(op IN ('>','<','>=','<=')),
            threshold REAL NOT NULL,
            severity TEXT DEFAULT 'info' CHECK(severity IN ('info','warn','critical')),
            message TEXT,
            enabled INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS thesis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER REFERENCES stocks(id),
            trade_id INTEGER REFERENCES trades(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            thesis TEXT NOT NULL,
            exit_condition TEXT,
            target_price REAL,
            stop_loss REAL,
            status TEXT DEFAULT 'active' CHECK(status IN ('active','closed','invalidated'))
        );
        """)

# ─── Pydantic models ──────────────────────────────────────

class StockIn(BaseModel):
    symbol: str
    name: str
    market: Literal["TW", "US"]
    sector: Optional[str] = None
    currency: Literal["TWD", "USD"] = "TWD"
    investment_style: Literal["dca", "thematic", "trade"] = "dca"

class TradeIn(BaseModel):
    stock_id: int
    trade_date: date
    action: Literal["buy", "sell", "dividend"]
    shares: float
    price: float
    fee: float = 0
    currency: Literal["TWD", "USD"] = "TWD"
    note: Optional[str] = None

class CashIn(BaseModel):
    date: date
    action: Literal["deposit", "withdraw", "stock_purchase", "stock_sell"]
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
                "INSERT INTO stocks (symbol,name,market,sector,currency,investment_style) VALUES (?,?,?,?,?,?)",
                (s.symbol, s.name, s.market, s.sector, s.currency, s.investment_style)
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
                   s.sector, s.investment_style
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
    token = token or FINMIND_TOKEN
    url = f"https://api.finmindtrade.com/api/v4/data?dataset={dataset}&data_id={symbol}&start_date={start}&end_date={end}&token={token}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())

# ─── Quote cache (in-memory TTL) ─────────────────────────
import time, sys
from concurrent.futures import ThreadPoolExecutor

_QUOTE_CACHE = {}  # key -> (expires_ts, value)
_TWSE_TTL = 30     # 秒
_AV_TTL = 120      # Alpha Vantage 限流嚴，快取久一點

def _cache_get(key):
    hit = _QUOTE_CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None

def _cache_set(key, value, ttl):
    _QUOTE_CACHE[key] = (time.time() + ttl, value)

_TWSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
    "Accept": "application/json, text/plain, */*",
}

def _twse_fetch_one(sym: str):
    """單檔查 TWSE MIS，先試 tse_ 再試 otc_；回傳 parsed dict 或 None"""
    for prefix in ("tse", "otc"):
        try:
            url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={prefix}_{sym}.tw&json=1&delay=0&_={int(time.time()*1000)}"
            req = urllib.request.Request(url, headers=_TWSE_HEADERS)
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read())
            arr = data.get("msgArray", [])
            if not arr:
                continue
            r0 = arr[0]
            def _f(key):
                s = (r0.get(key) or "").strip()
                try:
                    return float(s) if s and s != "-" else 0
                except ValueError:
                    return 0

            def _first(key):
                """a/b 欄位是 '1945.0000_1950.0000_...'，取第一個"""
                s = (r0.get(key) or "").strip()
                if not s or s == "-":
                    return 0
                first = s.split("_")[0].strip()
                try:
                    return float(first) if first and first != "-" else 0
                except ValueError:
                    return 0

            z = _f("z")    # 即時成交價
            pz = _f("pz")  # 最近一筆快取價
            y = _f("y")    # 昨收
            o = _f("o")    # 今開
            best_ask = _first("a")
            best_bid = _first("b")

            # 現價 fallback 鏈（盤中 z 常為空，要用買賣盤中價補）
            if z:
                price = z
            elif pz:
                price = pz
            elif best_bid and best_ask:
                price = round((best_bid + best_ask) / 2, 2)
            elif best_bid:
                price = best_bid
            elif best_ask:
                price = best_ask
            elif o:
                price = o   # 盤後才會 fallback 到今開
            elif y:
                price = y
            else:
                price = 0
            chg = round(price - y, 2) if price and y else 0
            chg_pct = round(chg / y * 100, 2) if y else 0
            return {
                "symbol": r0.get("c", sym),
                "name": (r0.get("n") or "").strip(),
                "price": price,
                "open": o,
                "high": _f("h"),
                "low": _f("l"),
                "volume": int(_f("v")),
                "change": chg,
                "change_pct": chg_pct,
                "prev_close": y,
                "bid": best_bid,
                "ask": best_ask,
                "time": r0.get("t", "") or r0.get("%", ""),
            }
        except Exception as e:
            print(f"[TWSE] {prefix}_{sym} error: {e}", file=sys.stderr)
            continue
    return None

def fetch_twse_realtime(symbol: str):
    """TWSE 即時報價（台股， symbol如 2330）"""
    cached = _cache_get(f"twse:{symbol}")
    if cached is not None:
        return cached
    res = _twse_fetch_one(symbol)
    if res is None:
        return {"error": "無資料"}
    _cache_set(f"twse:{symbol}", res, _TWSE_TTL)
    return res

def fetch_twse_batch(symbols: list):
    """批次查 TWSE 多檔即時報價（並行 + TTL 快取）"""
    result = {}
    missing = []
    for sym in symbols:
        c = _cache_get(f"twse:{sym}")
        if c is not None:
            result[sym] = c
        else:
            missing.append(sym)
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as ex:
            for sym, data in zip(missing, ex.map(_twse_fetch_one, missing)):
                if data is not None:
                    result[sym] = data
                    _cache_set(f"twse:{sym}", data, _TWSE_TTL)
    return result

def fetch_yahoo_quote(symbol: str):
    """Yahoo Finance 美股/ETF 即時報價（無需 API key，含 TTL 快取）"""
    cache_key = f"yh:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        res = (data.get("chart", {}).get("result") or [None])[0]
        if not res:
            return {"error": "無資料"}
        meta = res.get("meta", {}) or {}
        price = float(meta.get("regularMarketPrice") or 0)
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        chg = round(price - prev, 2) if price and prev else 0
        chg_pct = round(chg / prev * 100, 2) if prev else 0
        result = {
            "symbol": symbol,
            "price": price,
            "change": chg,
            "change_pct": chg_pct,
            "prev_close": prev,
            "time": str(meta.get("regularMarketTime") or ""),
        }
        if price > 0:
            _cache_set(cache_key, result, _TWSE_TTL)
        return result
    except Exception as e:
        print(f"[Yahoo] {symbol} error: {e}", file=sys.stderr)
        return {"error": str(e)}

def fetch_alphavantage(symbol: str, key: str = ""):
    """Alpha Vantage 美股報價（備援，25 req/天限制，含 TTL 快取）"""
    cache_key = f"av:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    key = key or ALPHA_KEY
    if not key:
        return {"error": "no_key"}
    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={key}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        if data.get("Note") or data.get("Information"):
            print(f"[AlphaVantage] rate-limited: {str(data)[:200]}", file=sys.stderr)
            return {"error": "rate_limited"}
        q = data.get("Global Quote", {}) or {}
        price = float(q.get("05. price") or 0)
        if price <= 0:
            return {"error": "無資料"}
        result = {
            "symbol": symbol,
            "price": price,
            "change": float(q.get("09. change") or 0),
            "change_pct": float((q.get("10. change percent") or "0").replace("%", "")),
        }
        _cache_set(cache_key, result, _AV_TTL)
        return result
    except Exception as e:
        print(f"[AlphaVantage] {symbol} error: {e}", file=sys.stderr)
        return {"error": str(e)}

def fetch_us_quote(symbol: str):
    """美股報價：Yahoo 主，Alpha Vantage 備援"""
    res = fetch_yahoo_quote(symbol)
    if res and res.get("price", 0) > 0:
        return res
    print(f"[Quote] Yahoo failed for {symbol}, falling back to Alpha Vantage", file=sys.stderr)
    return fetch_alphavantage(symbol)

# ─── 即時報價 API ────────────────────────────────────────
@app.get("/api/positions/rt")
def positions_with_realtime():
    """持股 + 即時報價 + 損益計算（從 stocks + trades 即時計算）"""
    with get_db() as db:
        # 從 stocks + trades 即時計算持股
        rows = db.execute("""
            SELECT
                s.id, s.symbol, s.name, s.market, s.sector, s.currency, s.investment_style,
                COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares WHEN t.action='sell' THEN -t.shares ELSE t.shares END),0) AS shares,
                COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares * t.price WHEN t.action='sell' THEN -t.shares * t.price ELSE 0 END),0) AS cost_basis
            FROM stocks s
            LEFT JOIN trades t ON s.id = t.stock_id AND t.action IN ('buy','sell')
            GROUP BY s.id
            HAVING shares > 0
        """).fetchall()

    positions = [dict(r) for r in rows]

    tw_map = {p["symbol"]: p for p in positions if p["currency"] != "USD"}
    us_map = {p["symbol"]: p for p in positions if p["currency"] == "USD"}

    # 批次 TWSE 即時報價
    if tw_map:
        tw_result = fetch_twse_batch(list(tw_map.keys()))
        for sym, d in tw_result.items():
            tw_map[sym]["_rt"] = d

    # 美股：Yahoo 主 / Alpha Vantage 備援
    for sym in us_map:
        d = fetch_us_quote(sym)
        if "error" not in d and d.get("price", 0) > 0:
            us_map[sym]["_rt"] = d

    all_data = {**tw_map, **us_map}
    out = []
    for p in positions:
        sym = p["symbol"]
        rt = all_data.get(sym, {}).get("_rt", {})
        cur_price = rt.get("price", 0) or 0
        shares = float(p["shares"])
        cost_basis = float(p["cost_basis"]) or 0
        avg_cost = (cost_basis / shares) if shares else 0
        cur_value = cur_price * shares
        pnl = cur_value - cost_basis
        pnl_pct = round(pnl / cost_basis * 100, 2) if cost_basis else 0
        out.append({
            **p,
            "shares": round(shares, 0),
            "avg_cost": round(avg_cost, 2),
            "cost_basis": round(cost_basis, 0),
            "current_price": cur_price,
            "current_value": round(cur_value, 0),
            "pnl": round(pnl, 0),
            "pnl_pct": pnl_pct,
            "change": rt.get("change") if rt else None,
            "change_pct": rt.get("change_pct") if rt else None,
            "time": rt.get("time") if rt else None,
        })
    return out

@app.get("/api/realtime/{symbol}")
def realtime_quote(symbol: str):
    """
    統一即時報價端點
    台股（4-6碼數字）→ TWSE
    美股（字母）→ Yahoo（AV 備援）
    """
    if symbol.isdigit() and len(symbol) <= 6:
        return fetch_twse_realtime(symbol)
    return fetch_us_quote(symbol)

@app.get("/api/realtime/batch")
def realtime_batch(symbols: str):
    """
    批次查詢即時報價
    ?symbols=2330,2883,AMD,TSLA
    """
    results = []
    for sym in symbols.split(","):
        sym = sym.strip()
        if not sym:
            continue
        if sym.isdigit() and len(sym) <= 6:
            results.append(fetch_twse_realtime(sym))
        else:
            results.append(fetch_us_quote(sym))
    return results

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
    style_map = {'dca': '存股', 'thematic': '題材/價差', 'trade': '短線交易'}
    style_label = style_map.get(stock.get('investment_style'), '存股')
    lines.append(f"股票：{stock['symbol']} {stock['name']}（{stock['sector'] or '其他'} | {style_label}）")
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
    return _no_cache_response(f"{STATIC_DIR}/fundamentals.html")

# ─── Static pages ──────────────────────────────────────────

def _no_cache_response(path):
    r = FileResponse(path)
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r

@app.get("/")
def root(): return _no_cache_response(f"{STATIC_DIR}/index.html")
@app.get("/dashboard")
def dashboard_page(): return _no_cache_response(f"{STATIC_DIR}/dashboard.html")
@app.get("/stocks")
def stocks_page(): return _no_cache_response(f"{STATIC_DIR}/stocks.html")
@app.get("/trades")
def trades_page(): return _no_cache_response(f"{STATIC_DIR}/trades.html")
@app.get("/positions")
def positions_page(): return _no_cache_response(f"{STATIC_DIR}/positions.html")

# ─── Ticker ───────────────────────────────────────────────
@app.get("/api/ticker")
def ticker_data():
    """跑馬燈：從使用者實際持股取市值最大的 TW+US 個股（自動更新）"""
    with get_db() as db:
        rows = db.execute("""
            SELECT s.symbol, s.name, s.currency,
                   COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares*t.price
                                     WHEN t.action='sell' THEN -t.shares*t.price
                                     ELSE 0 END), 0) AS cost_basis
            FROM stocks s
            LEFT JOIN trades t ON s.id = t.stock_id
            GROUP BY s.id
            HAVING COALESCE(SUM(CASE WHEN t.action='buy' THEN t.shares
                                     WHEN t.action='sell' THEN -t.shares
                                     ELSE 0 END), 0) > 0
            ORDER BY cost_basis DESC
            LIMIT 8
        """).fetchall()

    tw_syms = [r["symbol"] for r in rows if r["currency"] != "USD"]
    us_syms = [r["symbol"] for r in rows if r["currency"] == "USD"]

    name_map = {r["symbol"]: r["name"] for r in rows}

    result = []
    if tw_syms:
        tw_data = fetch_twse_batch(tw_syms)
        for sym in tw_syms:
            d = tw_data.get(sym)
            if not d:
                continue
            result.append({
                "symbol": sym,
                "name": d.get("name") or name_map.get(sym, sym),
                "price": d.get("price") or 0,
                "change": d.get("change"),
                "change_pct": d.get("change_pct") or 0,
            })
    for sym in us_syms:
        d = fetch_us_quote(sym)
        if "error" in d or not d.get("price"):
            continue
        result.append({
            "symbol": sym,
            "name": name_map.get(sym, sym),
            "price": d.get("price") or 0,
            "change": d.get("change"),
            "change_pct": d.get("change_pct") or 0,
        })
    return result

# ─── Settings helpers ─────────────────────────────────────

def setting_get(db, key: str, default=None):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def setting_set(db, key: str, value: str):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

# ─── Goal 2031 ───────────────────────────────────────────

@app.get("/api/goal/2031")
def goal_2031():
    """2031 目標進度：NAV 缺口 / CAGR 推算 / 被動收入估算"""
    from datetime import date as _date
    target_year = 2031
    today = _date.today()
    years_left = max(target_year - today.year, 1)

    with get_db() as db:
        target_nav = float(setting_get(db, "goal_2031_target_nav", "5000000"))
        target_passive = float(setting_get(db, "goal_2031_passive_income", "200000"))

        # 目前 NAV：優先用最新 nav_history，否則用 cost-basis fallback
        latest = db.execute("SELECT date, total_value FROM nav_history ORDER BY date DESC LIMIT 1").fetchone()
        first = db.execute("SELECT date, total_value FROM nav_history ORDER BY date ASC LIMIT 1").fetchone()

        if latest:
            current_nav = float(latest["total_value"])
        else:
            row = db.execute("""
                SELECT COALESCE(SUM(CASE WHEN action='deposit' THEN amount
                                         WHEN action IN ('withdraw','stock_purchase') THEN -amount
                                         WHEN action='stock_sell' THEN amount
                                         ELSE 0 END),0) AS cash FROM cash
            """).fetchone()
            current_nav = float(row["cash"] or 0)

        # 已實現 CAGR（若有兩筆以上 nav_history）
        realized_cagr = None
        projected_year = None
        if first and latest and first["date"] != latest["date"]:
            from datetime import datetime as _dt
            d0 = _dt.fromisoformat(first["date"]).date()
            d1 = _dt.fromisoformat(latest["date"]).date()
            days = (d1 - d0).days
            if days > 0 and float(first["total_value"]) > 0:
                ratio = current_nav / float(first["total_value"])
                if ratio > 0:
                    realized_cagr = ratio ** (365 / days) - 1
                    if realized_cagr > 0 and current_nav < target_nav:
                        # solve current * (1+r)^n = target → n = log(target/current)/log(1+r)
                        import math
                        n = math.log(target_nav / current_nav) / math.log(1 + realized_cagr)
                        projected_year = today.year + int(n)

        # 達標所需 CAGR
        gap = target_nav - current_nav
        implied_cagr = None
        if current_nav > 0 and gap > 0:
            implied_cagr = (target_nav / current_nav) ** (1 / years_left) - 1

        # 被動收入年化估算（持股 × 殖利率）— 沒抓殖利率時用 0
        # （fundamentals.dividend_yield 在 ai/stock 升級後會自動進來，這裡先給 0 placeholder）
        forward_dividend = 0.0
        ytd_dividend = float(db.execute("""
            SELECT COALESCE(SUM(t.shares * t.price), 0) AS d
            FROM trades t
            WHERE t.action='dividend' AND strftime('%Y', t.trade_date) = strftime('%Y','now')
        """).fetchone()["d"] or 0)

    return {
        "target_year": target_year,
        "target_nav": target_nav,
        "target_passive_income": target_passive,
        "current_nav": round(current_nav, 0),
        "gap": round(gap, 0),
        "years_left": years_left,
        "implied_cagr_required": round(implied_cagr, 4) if implied_cagr else None,
        "realized_cagr": round(realized_cagr, 4) if realized_cagr else None,
        "projected_target_year_at_current_pace": projected_year,
        "income": {
            "ytd_dividend": round(ytd_dividend, 0),
            "forward_annual_estimate": round(forward_dividend, 0),
            "passive_income_progress_pct": round(forward_dividend / target_passive * 100, 2) if target_passive else 0,
        },
        "note": "realized_cagr / projected 年至少要兩天 nav_history 才會出現" if realized_cagr is None else None,
    }


# ─── Init ─────────────────────────────────────────────────
init_db()

# ─── AI namespace router ──────────────────────────────────
from ai_routes import router as ai_router  # noqa: E402
app.include_router(ai_router)
