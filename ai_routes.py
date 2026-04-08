"""
/api/ai/* — AI 投資顧問專用 API namespace

設計原則：
- 每個 endpoint 都是「單次呼叫拿全 context」，避免 AI 多次往返
- 統一 Bearer token 認證 (env: AI_API_TOKEN)
- 預先計算好 derived metrics (集中度/drift/alerts/CAGR)，AI 不必再算
- 靜態資料 (價格/基本面) 沿用 main.py 的快取
"""
import os
import math
import statistics
from datetime import date as _date, datetime as _dt, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Query, Depends
from pydantic import BaseModel

# 沿用 main.py 既有 helpers
from main import (
    get_db,
    fetch_twse_batch,
    fetch_us_quote,
    fetch_yahoo_quote,
    fetch_finmind,
    fetch_twse_realtime,
    setting_get,
    _cache_get,
    _cache_set,
)

router = APIRouter(prefix="/api/ai", tags=["ai"])


# ─── Auth dependency ──────────────────────────────────────

def require_token(authorization: Optional[str] = Header(None)):
    expected = os.getenv("AI_API_TOKEN", "")
    if not expected:
        raise HTTPException(500, "AI_API_TOKEN not configured on server")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(401, "Invalid token")
    return True


# ─── Internal helpers ─────────────────────────────────────

def _holdings(db):
    return db.execute("""
        SELECT s.id, s.symbol, s.name, s.market, s.sector, s.currency, s.investment_style,
               SUM(CASE WHEN t.action='buy' THEN t.shares
                        WHEN t.action='sell' THEN -t.shares ELSE 0 END) AS shares,
               SUM(CASE WHEN t.action='buy' THEN t.shares*t.price
                        WHEN t.action='sell' THEN -t.shares*t.price ELSE 0 END) AS cost_basis
        FROM stocks s
        JOIN trades t ON s.id = t.stock_id
        GROUP BY s.id
        HAVING shares > 0
    """).fetchall()


def _cash(db) -> float:
    row = db.execute("""
        SELECT COALESCE(SUM(CASE
            WHEN action='deposit' THEN amount
            WHEN action IN ('withdraw','stock_purchase') THEN -amount
            WHEN action='stock_sell' THEN amount
            ELSE 0 END), 0) AS cash FROM cash
    """).fetchone()
    return float(row["cash"] or 0)


def _usd_twd():
    return float(fetch_yahoo_quote("TWD=X").get("price") or 30.0)


def _live_prices(rows):
    """rows: holdings rows. 回 {symbol: price (原幣別)}"""
    tw = [r["symbol"] for r in rows if r["currency"] != "USD"]
    us = [r["symbol"] for r in rows if r["currency"] == "USD"]
    out = {}
    if tw:
        for sym, d in fetch_twse_batch(tw).items():
            if d.get("price"):
                out[sym] = d["price"]
    for sym in us:
        d = fetch_us_quote(sym)
        if d.get("price"):
            out[sym] = d["price"]
    return out


def _eval_alerts(db, ctx: dict) -> list:
    """從 alert_rules 表評估觸發中的規則。ctx 是 portfolio + per-stock metrics 的合併字典。"""
    rules = db.execute("SELECT * FROM alert_rules WHERE enabled=1").fetchall()
    triggered = []
    ops = {">": lambda a, b: a > b, "<": lambda a, b: a < b,
           ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}
    for r in rules:
        scope = r["scope"]
        target = r["target"] or ""
        metric = r["metric"]
        op = r["op"]
        thr = float(r["threshold"])
        # ctx 結構：portfolio.<metric> 或 stock.<symbol>.<metric>
        key = f"{scope}.{target}.{metric}" if scope == "stock" else f"portfolio.{metric}"
        val = ctx.get(key)
        if val is None:
            continue
        try:
            if ops[op](float(val), thr):
                triggered.append({
                    "severity": r["severity"],
                    "scope": scope,
                    "target": target,
                    "metric": metric,
                    "op": op,
                    "threshold": thr,
                    "actual": round(float(val), 2),
                    "message": r["message"] or f"{target} {metric} {op} {thr}",
                })
        except (ValueError, TypeError):
            continue
    return triggered


def _drift(db, by_sector: dict, by_style: dict, by_currency: dict, positions: list) -> list:
    """vs allocation_targets 計算 drift。回 list of {scope/key/target/current/diff/action}"""
    targets = db.execute("SELECT scope, key, target_pct FROM allocation_targets").fetchall()
    actual_map = {
        ("sector", k): v for k, v in by_sector.items()
    }
    actual_map.update({("style", k): v for k, v in by_style.items()})
    actual_map.update({("region", k): v for k, v in by_currency.items()})
    for p in positions:
        actual_map[("stock", p["symbol"])] = p["weight_pct"]

    out = []
    for t in targets:
        cur = actual_map.get((t["scope"], t["key"]), 0.0)
        diff = round(cur - float(t["target_pct"]), 2)
        out.append({
            "scope": t["scope"],
            "key": t["key"],
            "target_pct": float(t["target_pct"]),
            "current_pct": round(cur, 2),
            "diff_pct": diff,
            "action": "reduce" if diff > 1 else "buy" if diff < -1 else "hold",
        })
    return out


# ─── 2.1 Portfolio ────────────────────────────────────────

@router.get("/portfolio", dependencies=[Depends(require_token)])
def ai_portfolio():
    with get_db() as db:
        rows = _holdings(db)
        if not rows:
            return {"snapshot_at": _dt.now().isoformat(),
                    "nav": {"total": 0, "cash": 0, "equity": 0},
                    "positions": [], "concentration": {}, "performance": {},
                    "income": {}, "drift": [], "alerts": [], "goal_2031": None}

        prices = _live_prices(rows)
        usd_twd = _usd_twd() if any(r["currency"] == "USD" for r in rows) else 1.0
        cash = _cash(db)

        positions = []
        equity = 0.0
        for r in rows:
            sym = r["symbol"]
            shares = float(r["shares"])
            cost_local = float(r["cost_basis"])
            price = prices.get(sym, 0)
            fx = usd_twd if r["currency"] == "USD" else 1.0
            mkt_twd = price * shares * fx
            cost_twd = cost_local * fx
            pnl_twd = mkt_twd - cost_twd
            pnl_pct = round(pnl_twd / cost_twd * 100, 2) if cost_twd else 0
            positions.append({
                "symbol": sym, "name": r["name"], "market": r["market"],
                "sector": r["sector"], "currency": r["currency"],
                "investment_style": r["investment_style"],
                "shares": round(shares, 4),
                "avg_cost": round(cost_local / shares, 2) if shares else 0,
                "current_price": price,
                "cost_twd": round(cost_twd, 0),
                "market_value_twd": round(mkt_twd, 0),
                "pnl_twd": round(pnl_twd, 0),
                "pnl_pct": pnl_pct,
            })
            equity += mkt_twd

        total = equity + cash
        # weights
        for p in positions:
            p["weight_pct"] = round(p["market_value_twd"] / total * 100, 2) if total else 0

        # concentration
        sorted_by_weight = sorted(positions, key=lambda x: -x["weight_pct"])
        top1 = sorted_by_weight[0]["weight_pct"] if sorted_by_weight else 0
        top3 = sum(p["weight_pct"] for p in sorted_by_weight[:3])
        by_sector = {}
        by_style = {}
        by_currency = {}
        for p in positions:
            by_sector[p["sector"] or "其他"] = by_sector.get(p["sector"] or "其他", 0) + p["weight_pct"]
            by_style[p["investment_style"] or "dca"] = by_style.get(p["investment_style"] or "dca", 0) + p["weight_pct"]
            by_currency[p["currency"]] = by_currency.get(p["currency"], 0) + p["weight_pct"]
        # cash 也算進 currency 配置
        if total:
            by_currency["TWD"] = by_currency.get("TWD", 0) + round(cash / total * 100, 2)
        # HHI
        hhi = round(sum((w/100) ** 2 for w in (p["weight_pct"] for p in positions)) * 10000, 0)

        concentration = {
            "top1_pct": top1,
            "top3_pct": round(top3, 2),
            "hhi": hhi,
            "by_sector": [{"sector": k, "pct": round(v, 2)} for k, v in sorted(by_sector.items(), key=lambda x: -x[1])],
            "by_style": {k: round(v, 2) for k, v in by_style.items()},
            "by_currency": {k: round(v, 2) for k, v in by_currency.items()},
        }

        # performance from nav_history
        navs = db.execute("SELECT date, total_value, twii_close FROM nav_history ORDER BY date ASC").fetchall()
        perf = {"data_points": len(navs)}
        if len(navs) >= 2:
            first_v = float(navs[0]["total_value"])
            last_v = float(navs[-1]["total_value"])
            perf["since_inception_pct"] = round((last_v / first_v - 1) * 100, 2) if first_v else None
            # YTD
            year_start = next((n for n in navs if n["date"].startswith(str(_date.today().year))), navs[0])
            if float(year_start["total_value"]):
                perf["ytd_pct"] = round((last_v / float(year_start["total_value"]) - 1) * 100, 2)
            # 30 day
            cutoff = (_date.today() - timedelta(days=30)).isoformat()
            past = next((n for n in navs if n["date"] >= cutoff), navs[0])
            if float(past["total_value"]):
                perf["30d_pct"] = round((last_v / float(past["total_value"]) - 1) * 100, 2)
                if past["twii_close"] and navs[-1]["twii_close"]:
                    twii_30d = (float(navs[-1]["twii_close"]) / float(past["twii_close"]) - 1) * 100
                    perf["vs_twii_30d_pct"] = round(perf["30d_pct"] - twii_30d, 2)
            # max drawdown
            peak = -1e18
            mdd = 0
            for n in navs:
                v = float(n["total_value"])
                if v > peak:
                    peak = v
                if peak > 0:
                    dd = (v - peak) / peak * 100
                    if dd < mdd:
                        mdd = dd
            perf["max_drawdown_pct"] = round(mdd, 2)
            perf["current_drawdown_pct"] = round((last_v - peak) / peak * 100, 2) if peak else 0

        # income
        ytd_div = float(db.execute("""
            SELECT COALESCE(SUM(t.shares * t.price), 0) AS d FROM trades t
            WHERE t.action='dividend' AND strftime('%Y', t.trade_date) = strftime('%Y','now')
        """).fetchone()["d"] or 0)
        target_passive = float(setting_get(db, "goal_2031_passive_income", "200000"))
        income = {
            "ytd_dividend_twd": round(ytd_div, 0),
            "forward_annual_estimate_twd": 0,  # 需殖利率 → 後續 ai/stock 升級時補
            "passive_income_progress_pct": round(ytd_div / target_passive * 100, 2) if target_passive else 0,
        }

        # drift
        drift = _drift(db, by_sector, by_style, by_currency, positions)

        # alerts ctx
        ctx = {
            "portfolio.cash_pct": round(cash / total * 100, 2) if total else 0,
            "portfolio.top1_pct": top1,
            "portfolio.top3_pct": round(top3, 2),
            "portfolio.hhi": hhi,
            "portfolio.drawdown": perf.get("current_drawdown_pct", 0),
        }
        for sec, pct in by_sector.items():
            ctx[f"portfolio.sector_pct_{sec}"] = pct
        for p in positions:
            ctx[f"stock.{p['symbol']}.pnl_pct"] = p["pnl_pct"]
            ctx[f"stock.{p['symbol']}.weight_pct"] = p["weight_pct"]
        alerts = _eval_alerts(db, ctx)

        # goal 2031
        target_nav = float(setting_get(db, "goal_2031_target_nav", "5000000"))
        years_left = max(2031 - _date.today().year, 1)
        gap = target_nav - total
        implied = ((target_nav / total) ** (1 / years_left) - 1) if total > 0 and gap > 0 else None
        goal = {
            "target_nav": target_nav,
            "current_nav": round(total, 0),
            "gap": round(gap, 0),
            "years_left": years_left,
            "implied_cagr_required": round(implied, 4) if implied else None,
        }

        return {
            "snapshot_at": _dt.now().isoformat(),
            "nav": {"total": round(total, 0), "cash": round(cash, 0), "equity": round(equity, 0)},
            "positions": positions,
            "concentration": concentration,
            "performance": perf,
            "income": income,
            "drift": drift,
            "alerts": alerts,
            "goal_2031": goal,
        }


# ─── 2.2 Stock context ────────────────────────────────────

@router.get("/stock/{symbol}", dependencies=[Depends(require_token)])
def ai_stock(symbol: str):
    """單檔決策級 context — symbol 走代碼"""
    with get_db() as db:
        stock = db.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
        if not stock:
            raise HTTPException(404, f"Stock {symbol} not registered")
        stock = dict(stock)

        # 持股
        h = db.execute("""
            SELECT SUM(CASE WHEN action='buy' THEN shares WHEN action='sell' THEN -shares ELSE 0 END) AS shares,
                   SUM(CASE WHEN action='buy' THEN shares*price WHEN action='sell' THEN -shares*price ELSE 0 END) AS cost
            FROM trades WHERE stock_id=?
        """, (stock["id"],)).fetchone()
        shares = float(h["shares"] or 0)
        cost = float(h["cost"] or 0)

        # thesis
        thesis_rows = db.execute("""
            SELECT thesis, exit_condition, target_price, stop_loss, status, created_at
            FROM thesis WHERE stock_id=? AND status='active' ORDER BY created_at DESC
        """, (stock["id"],)).fetchall()

    # 即時報價
    if stock["market"] == "TW":
        rt = fetch_twse_realtime(stock["symbol"])
    else:
        rt = fetch_us_quote(stock["symbol"])
    current_price = rt.get("price") or 0

    # Fundamentals (TW only)
    valuation = {"per": None, "pbr": None, "dividend_yield": None, "price_52w_high": None, "price_52w_low": None}
    institutional = []
    revenue = []
    if stock["market"] == "TW":
        today = _date.today()
        try:
            data = fetch_finmind("TaiwanStockPER", stock["symbol"], (today - timedelta(days=30)).isoformat(), today.isoformat())
            if data.get("data"):
                latest = data["data"][-1]
                valuation["per"] = latest.get("PER")
                valuation["pbr"] = latest.get("PBR")
                valuation["dividend_yield"] = latest.get("dividend_yield")
        except Exception:
            pass
        try:
            data = fetch_finmind("TaiwanStockPrice", stock["symbol"], (today - timedelta(days=365)).isoformat(), today.isoformat())
            prices = [float(d["close"]) for d in (data.get("data") or []) if d.get("close")]
            if prices:
                valuation["price_52w_high"] = max(prices)
                valuation["price_52w_low"] = min(prices)
                if current_price and len(prices) >= 30:
                    mean = statistics.mean(prices)
                    stdev = statistics.pstdev(prices) or 1
                    valuation["price_zscore"] = round((current_price - mean) / stdev, 2)
        except Exception:
            pass
        try:
            data = fetch_finmind("TaiwanStockInstitutionalInvestorsBuySell", stock["symbol"], (today - timedelta(days=14)).isoformat(), today.isoformat())
            by_date = {}
            for r in (data.get("data") or []):
                d = r["date"]
                buy, sell = int(r.get("buy") or 0), int(r.get("sell") or 0)
                name = r.get("name", "")
                by_date.setdefault(d, {"date": d, "foreign_net": 0, "trust_net": 0, "dealer_net": 0})
                if "Foreign" in name:
                    by_date[d]["foreign_net"] += buy - sell
                elif "Investment_Trust" in name:
                    by_date[d]["trust_net"] += buy - sell
                elif "Dealer" in name:
                    by_date[d]["dealer_net"] += buy - sell
            institutional = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)[:5]
        except Exception:
            pass
        try:
            data = fetch_finmind("TaiwanStockMonthRevenue", stock["symbol"], (today - timedelta(days=400)).isoformat(), today.isoformat())
            rows = (data.get("data") or [])[-12:]
            for i, r in enumerate(rows):
                rev = float(r.get("revenue") or 0)
                prev = float(rows[i-1]["revenue"]) if i > 0 and rows[i-1].get("revenue") else rev
                mom = round((rev - prev) / prev * 100, 1) if prev else 0
                revenue.append({"date": r["date"], "revenue": rev, "mom_pct": mom})
        except Exception:
            pass

    # alerts on this stock
    with get_db() as db:
        ctx = {
            f"stock.{symbol}.pe": valuation.get("per"),
            f"stock.{symbol}.pbr": valuation.get("pbr"),
            f"stock.{symbol}.yield": valuation.get("dividend_yield"),
            f"stock.{symbol}.price_zscore": valuation.get("price_zscore"),
        }
        if shares > 0:
            ctx[f"stock.{symbol}.pnl_pct"] = round((current_price * shares - cost) / cost * 100, 2) if cost else 0
        alerts = _eval_alerts(db, ctx)

    return {
        "snapshot_at": _dt.now().isoformat(),
        "stock": stock,
        "quote": {
            "price": current_price,
            "change": rt.get("change"),
            "change_pct": rt.get("change_pct"),
            "prev_close": rt.get("prev_close"),
        },
        "holding": {
            "shares": shares,
            "avg_cost": round(cost / shares, 2) if shares else 0,
            "cost_basis": round(cost, 0),
            "current_value": round(current_price * shares, 0),
            "unrealized_pnl": round(current_price * shares - cost, 0),
            "unrealized_pnl_pct": round((current_price * shares - cost) / cost * 100, 2) if cost else 0,
        } if shares > 0 else None,
        "valuation": valuation,
        "institutional_5d": institutional,
        "revenue_12m": revenue,
        "thesis": [dict(t) for t in thesis_rows],
        "alerts": alerts,
    }


# ─── 2.3 Risk ─────────────────────────────────────────────

@router.get("/risk", dependencies=[Depends(require_token)])
def ai_risk():
    with get_db() as db:
        navs = db.execute("SELECT date, total_value, twii_close FROM nav_history ORDER BY date ASC").fetchall()

    if len(navs) < 2:
        return {"data_points": len(navs), "note": "需至少 2 天 nav_history 才能算風險指標"}

    values = [float(n["total_value"]) for n in navs]
    returns = [(values[i] / values[i-1] - 1) for i in range(1, len(values)) if values[i-1]]

    def vol_n(n):
        if len(returns) < n:
            return None
        sample = returns[-n:]
        return round(statistics.pstdev(sample) * math.sqrt(252) * 100, 2)

    # max drawdown
    peak = values[0]
    mdd = 0
    for v in values:
        peak = max(peak, v)
        dd = (v - peak) / peak if peak else 0
        if dd < mdd:
            mdd = dd

    # beta vs TWII
    beta = None
    twii = [float(n["twii_close"]) for n in navs if n["twii_close"]]
    if len(twii) == len(values) and len(twii) >= 30:
        twii_ret = [(twii[i] / twii[i-1] - 1) for i in range(1, len(twii)) if twii[i-1]]
        if len(twii_ret) == len(returns) and len(returns) > 1:
            cov = statistics.covariance(returns, twii_ret) if hasattr(statistics, "covariance") else \
                  sum((r - statistics.mean(returns)) * (m - statistics.mean(twii_ret)) for r, m in zip(returns, twii_ret)) / (len(returns) - 1)
            var_m = statistics.pvariance(twii_ret)
            beta = round(cov / var_m, 2) if var_m else None

    return {
        "data_points": len(navs),
        "vol_30d_annualized_pct": vol_n(30),
        "vol_90d_annualized_pct": vol_n(90),
        "max_drawdown_pct": round(mdd * 100, 2),
        "current_drawdown_pct": round((values[-1] - peak) / peak * 100, 2) if peak else 0,
        "beta_vs_twii": beta,
    }


# ─── 2.4 Screen ───────────────────────────────────────────

@router.get("/screen", dependencies=[Depends(require_token)])
def ai_screen(
    market: Optional[str] = Query(None, description="TW / US"),
    style: Optional[str] = Query(None, description="dca / thematic / trade"),
    sector: Optional[str] = Query(None),
    max_pe: Optional[float] = Query(None),
    min_yield: Optional[float] = Query(None),
    held_only: bool = Query(False, description="只看已持有"),
):
    """從已註冊股票中篩選候選 — 支援多條件過濾。FinMind 只支援台股估值。"""
    with get_db() as db:
        q = "SELECT * FROM stocks WHERE 1=1"
        params = []
        if market:
            q += " AND market=?"; params.append(market)
        if sector:
            q += " AND sector=?"; params.append(sector)
        if style:
            q += " AND investment_style=?"; params.append(style)
        stocks = db.execute(q, params).fetchall()

        held_ids = {r["id"] for r in db.execute("""
            SELECT s.id FROM stocks s JOIN trades t ON s.id=t.stock_id
            GROUP BY s.id
            HAVING SUM(CASE WHEN t.action='buy' THEN t.shares WHEN t.action='sell' THEN -t.shares ELSE 0 END) > 0
        """).fetchall()}

    candidates = []
    today = _date.today()
    for s in stocks:
        s = dict(s)
        is_held = s["id"] in held_ids
        if held_only and not is_held:
            continue
        # 估值（只 TW）
        per, pbr, dy = None, None, None
        if s["market"] == "TW":
            try:
                data = fetch_finmind("TaiwanStockPER", s["symbol"], (today - timedelta(days=14)).isoformat(), today.isoformat())
                if data.get("data"):
                    latest = data["data"][-1]
                    per = latest.get("PER")
                    pbr = latest.get("PBR")
                    dy = latest.get("dividend_yield")
            except Exception:
                pass
        if max_pe is not None and (per is None or per > max_pe):
            continue
        if min_yield is not None and (dy is None or dy < min_yield):
            continue
        candidates.append({
            "symbol": s["symbol"], "name": s["name"], "market": s["market"],
            "sector": s["sector"], "investment_style": s["investment_style"],
            "is_held": is_held, "per": per, "pbr": pbr, "dividend_yield": dy,
        })
    # 排序：未持有優先 → PE 低優先
    candidates.sort(key=lambda x: (x["is_held"], x["per"] if x["per"] is not None else 999))
    return {"count": len(candidates), "candidates": candidates}


# ─── 2.5 Journal (write thesis back) ──────────────────────

class ThesisIn(BaseModel):
    symbol: str
    thesis: str
    exit_condition: Optional[str] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    trade_id: Optional[int] = None


@router.post("/journal", dependencies=[Depends(require_token)])
def ai_journal(t: ThesisIn):
    with get_db() as db:
        stock = db.execute("SELECT id FROM stocks WHERE symbol=?", (t.symbol,)).fetchone()
        if not stock:
            raise HTTPException(404, f"Stock {t.symbol} not registered")
        cur = db.execute("""
            INSERT INTO thesis (stock_id, trade_id, thesis, exit_condition, target_price, stop_loss, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
        """, (stock["id"], t.trade_id, t.thesis, t.exit_condition, t.target_price, t.stop_loss))
        db.commit()
        return {"id": cur.lastrowid, "ok": True}
