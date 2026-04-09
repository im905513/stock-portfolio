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
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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
    fetch_finmind_stock_info,
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
    """從已註冊股票中篩選候選 — 支援多條件過濾。FinMind 只支援台股估值。
    僅看 watch_status='active'，watchlist 候選請改用 /discover。"""
    with get_db() as db:
        q = "SELECT * FROM stocks WHERE COALESCE(watch_status,'active')='active'"
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


# ─── 2.6 Discover (DCA candidate mining) ──────────────────
#
# 與 /screen 的差異：
#   /screen   = 從 DB 已註冊股票 SELECT，受限於追蹤池
#   /discover = 從 FinMind TaiwanStockInfo 全市場挖掘，再評分
#
# DCA 適合度評分 (0-1)：
#   殖利率 ≥ 4%             權重 0.35
#   PE 落在 10-20 合理區間   權重 0.25
#   近 12 月有營收紀錄       權重 0.20  (FinMind dividend 資料集穩定性差，暫用營收存在性代理)
#   PBR ≤ 2                 權重 0.20
#
# 缺資料時不直接歸零，會降權 (回傳 score 仍可用)。

# 預設候選池 — 擴充到 FinMind 常見所有「相對穩定、適合 DCA」的產業分類
# 對 FinMind industry_category 做 substring 比對 (大小寫不敏感)
_DCA_INDUSTRY_KEYWORDS = (
    # 金融相關
    "金融", "金控", "銀行", "保險", "證券", "Financial", "Finance", "Bank", "Insurance",
    # 食品 / 民生
    "食品", "Food", "貿易百貨", "百貨", "Retail",
    # 科技 / 電子
    "電子", "半導體", "Semiconductor", "電腦", "通信", "光電", "電機", "資訊服務",
    # 電信 / 公用
    "電信", "Telecom", "公用", "油電燃氣", "Utilit",
    # 原物料 / 工業
    "塑膠", "Plastic", "鋼鐵", "Steel", "水泥", "Cement", "紡織", "造紙", "玻璃", "化學", "化工",
    # 運輸 / 觀光
    "航運", "Shipping", "汽車", "Auto", "觀光", "Tourism",
    # 生技 / 建材
    "生技", "醫療", "Biotech", "Medical", "建材", "營造", "Construction",
)

# 明顯不適合 DCA 的標的 (ETF / 權證 / 特別股)
_DCA_EXCLUDE_KEYWORDS = (
    "ETF", "ETN", "權證", "特別股", "受益憑證", "存託憑證",
)


def _dca_score(per, pbr, dy, has_revenue):
    """0-1。缺值用降權，不歸零。回 (score, reasons)。"""
    score = 0.0
    weight_used = 0.0
    reasons = []

    # 殖利率 (0.35)
    if dy is not None:
        weight_used += 0.35
        if dy >= 5:
            score += 0.35; reasons.append(f"殖利率 {dy:.2f}% 優異")
        elif dy >= 4:
            score += 0.30; reasons.append(f"殖利率 {dy:.2f}% 達標")
        elif dy >= 3:
            score += 0.18; reasons.append(f"殖利率 {dy:.2f}% 普通")
        else:
            score += 0.05; reasons.append(f"殖利率 {dy:.2f}% 偏低")

    # PE (0.25) — 合理區間 10-20
    if per is not None and per > 0:
        weight_used += 0.25
        if 10 <= per <= 20:
            score += 0.25; reasons.append(f"PE {per:.1f} 合理區")
        elif 8 <= per < 10 or 20 < per <= 25:
            score += 0.18; reasons.append(f"PE {per:.1f} 邊緣")
        elif per < 8:
            score += 0.12; reasons.append(f"PE {per:.1f} 偏低 (留意基本面)")
        else:
            score += 0.05; reasons.append(f"PE {per:.1f} 偏貴")

    # PBR (0.20)
    if pbr is not None and pbr > 0:
        weight_used += 0.20
        if pbr <= 1.5:
            score += 0.20; reasons.append(f"PBR {pbr:.2f} 便宜")
        elif pbr <= 2:
            score += 0.14; reasons.append(f"PBR {pbr:.2f} 合理")
        elif pbr <= 3:
            score += 0.07; reasons.append(f"PBR {pbr:.2f} 偏貴")
        else:
            score += 0.02; reasons.append(f"PBR {pbr:.2f} 高估")

    # 營收資料存在性 (0.20)
    if has_revenue is not None:
        weight_used += 0.20
        if has_revenue:
            score += 0.20; reasons.append("近 12 月營收正常揭露")
        else:
            score += 0.05

    # 全部缺資料的話直接 0
    if weight_used == 0:
        return 0.0, ["無 FinMind 資料"]
    # 降權後的 score 還原到 0-1 區間（避免缺資料股票分數被嚴重低估）
    normalized = round(score / weight_used, 3)
    return normalized, reasons


def _fetch_dca_metrics(symbol):
    """單檔抓 PE/PBR/yield + 是否有近期營收。失敗回 None 們。"""
    today = _date.today()
    per = pbr = dy = None
    has_revenue = None
    try:
        data = fetch_finmind("TaiwanStockPER", symbol, (today - timedelta(days=14)).isoformat(), today.isoformat())
        if data.get("data"):
            latest = data["data"][-1]
            per = latest.get("PER")
            pbr = latest.get("PBR")
            dy = latest.get("dividend_yield")
    except Exception:
        pass
    try:
        data = fetch_finmind("TaiwanStockMonthRevenue", symbol, (today - timedelta(days=120)).isoformat(), today.isoformat())
        rows = data.get("data") or []
        has_revenue = len(rows) > 0
    except Exception:
        pass
    return per, pbr, dy, has_revenue


@router.get("/discover", dependencies=[Depends(require_token)])
def ai_discover(
    market: str = Query("TW", description="目前只支援 TW (FinMind)"),
    min_score: float = Query(0.5, ge=0, le=1),
    limit: int = Query(20, ge=1, le=50),
    pool: int = Query(80, ge=5, le=200, description="候選池大小 (FinMind 抓取上限)"),
    exclude_held: bool = Query(True),
    industry: Optional[str] = Query(None, description="產業過濾：指定關鍵字=子字串比對；'all'=跳過白名單掃全市場；留空=用預設 DCA 白名單"),
):
    """從 FinMind 全台股清單挖掘適合定期定額的候選，依 DCA score 排序回傳。

    第一次跑可能 ~10-20s (併發 FinMind)。結果 1h cache。分層取樣避免單一產業吃掉 quota。
    """
    if market != "TW":
        raise HTTPException(400, "目前只支援 market=TW")

    industry_norm = (industry or "").strip()
    cache_industry_tag = industry_norm.lower() or "whitelist"
    cache_key = f"ai_discover:{market}:{cache_industry_tag}:{pool}"
    cached = _cache_get(cache_key)
    if cached is None:
        # 取全市場清單
        try:
            all_stocks = fetch_finmind_stock_info()
        except Exception as e:
            raise HTTPException(503, f"FinMind TaiwanStockInfo 取得失敗: {e}")

        scan_all = industry_norm.lower() == "all"

        # 過濾：產業關鍵字 + 排除不適合 DCA 的標的
        def _match_industry(s):
            ind = s.get("industry") or ""
            name = s.get("name") or ""
            # 永遠排除 ETF / 權證 / 特別股
            if any(k in name or k in ind for k in _DCA_EXCLUDE_KEYWORDS):
                return False
            if scan_all:
                return bool(ind)  # 只保留有產業分類的
            if industry_norm:
                return industry_norm.lower() in ind.lower()
            return any(k.lower() in ind.lower() for k in _DCA_INDUSTRY_KEYWORDS)

        matched = [s for s in all_stocks if _match_industry(s)]

        # 分層取樣：依 industry_category 分組，每組取前 N 檔 (symbol 排序)
        groups = defaultdict(list)
        for s in matched:
            groups[s.get("industry") or "Unknown"].append(s)
        for k in groups:
            groups[k].sort(key=lambda x: x.get("symbol") or "")

        group_count = max(1, len(groups))
        per_group = max(3, pool // group_count)
        candidates_pool = []
        for ind_name, items in groups.items():
            candidates_pool.extend(items[:per_group])
        # 若分層後超量，截到 pool；若不足，補其他剩餘股票
        candidates_pool.sort(key=lambda x: x.get("symbol") or "")
        if len(candidates_pool) > pool:
            candidates_pool = candidates_pool[:pool]
        elif len(candidates_pool) < pool:
            seen = {c["symbol"] for c in candidates_pool}
            for s in matched:
                if s["symbol"] not in seen:
                    candidates_pool.append(s)
                    if len(candidates_pool) >= pool:
                        break

        # 併發抓指標
        def _enrich(s):
            per, pbr, dy, has_rev = _fetch_dca_metrics(s["symbol"])
            score, reasons = _dca_score(per, pbr, dy, has_rev)
            return {
                "symbol": s["symbol"],
                "name": s["name"],
                "industry": s["industry"],
                "per": per, "pbr": pbr, "dividend_yield": dy,
                "score": score,
                "reasons": reasons,
            }

        results = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            for row in ex.map(_enrich, candidates_pool):
                results.append(row)
        results.sort(key=lambda x: -x["score"])
        _cache_set(cache_key, results, 3600)
        cached = results

    # 排除已持有 / watch_status='active'
    excluded = set()
    if exclude_held:
        with get_db() as db:
            held = db.execute("SELECT symbol FROM stocks WHERE watch_status='active'").fetchall()
            excluded = {r["symbol"] for r in held}

    # 標記哪些已在 watchlist
    with get_db() as db:
        watch_rows = db.execute("SELECT symbol FROM stocks WHERE watch_status='watchlist'").fetchall()
        in_watchlist = {r["symbol"] for r in watch_rows}

    # 讀取 valuations 快取表，補上估價標籤
    valuation_map = {}
    with get_db() as db:
        rows = db.execute(
            "SELECT symbol, tag, cheap_price, fair_price, expensive_price, current_price, category, method FROM valuations"
        ).fetchall()
        for r in rows:
            valuation_map[r["symbol"]] = dict(r)

    filtered = []
    for c in cached:
        if c["symbol"] in excluded:
            continue
        if c["score"] < min_score:
            continue
        v = valuation_map.get(c["symbol"]) or {}
        filtered.append({
            **c,
            "in_watchlist": c["symbol"] in in_watchlist,
            "valuation_tag": v.get("tag"),
            "cheap_price": v.get("cheap_price"),
            "fair_price": v.get("fair_price"),
            "expensive_price": v.get("expensive_price"),
            "valuation_category": v.get("category"),
        })
        if len(filtered) >= limit:
            break

    breakdown = defaultdict(int)
    for c in filtered:
        breakdown[c.get("industry") or "Unknown"] += 1

    return {
        "snapshot_at": _dt.now().isoformat(),
        "market": market,
        "min_score": min_score,
        "pool_size": len(cached),
        "count": len(filtered),
        "industry_breakdown": dict(breakdown),
        "candidates": filtered,
    }


# ─── 2.7 Watchlist add ────────────────────────────────────

class WatchlistAddIn(BaseModel):
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    market: str = "TW"


@router.post("/watchlist/add", dependencies=[Depends(require_token)])
def ai_watchlist_add(w: WatchlistAddIn):
    """加入 watchlist。若 DB 沒有該 symbol，從 FinMind TaiwanStockInfo 補資料後 INSERT。"""
    sym = w.symbol.strip()
    if not sym:
        raise HTTPException(400, "symbol 不能為空")

    name = w.name
    sector = w.sector
    market = w.market

    with get_db() as db:
        existing = db.execute("SELECT * FROM stocks WHERE symbol=?", (sym,)).fetchone()
        if existing:
            existing = dict(existing)
            cur_status = existing.get("watch_status", "active")
            if cur_status == "active":
                # 已持有 — 不該被加入 watchlist
                return {"ok": True, "stock": existing, "note": "already active (held)"}
            if cur_status != "watchlist":
                db.execute("UPDATE stocks SET watch_status='watchlist' WHERE id=?", (existing["id"],))
                db.commit()
                existing["watch_status"] = "watchlist"
            return {"ok": True, "stock": existing, "note": "moved to watchlist"}

        # 新股 — 補 name/sector
        if not name or not sector:
            try:
                info_list = fetch_finmind_stock_info()
                hit = next((x for x in info_list if x["symbol"] == sym), None)
                if hit:
                    name = name or hit["name"]
                    sector = sector or hit["industry"]
            except Exception:
                pass
        if not name:
            raise HTTPException(400, f"無法取得 {sym} 的名稱，請手動提供 name")

        currency = "USD" if market == "US" else "TWD"
        cur = db.execute(
            "INSERT INTO stocks (symbol, name, market, sector, currency, investment_style, watch_status) VALUES (?,?,?,?,?,?,?)",
            (sym, name, market, sector, currency, "dca", "watchlist"),
        )
        db.commit()
        new_row = db.execute("SELECT * FROM stocks WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"ok": True, "stock": dict(new_row), "note": "created"}
