#!/usr/bin/env python3
"""
每日 NAV 快照
- 對每檔持股寫一筆 positions(date, stock_id, shares, avg_cost, market_value, pnl, pnl_pct)
- 寫一筆 nav_history(date, total_value, cash, equity_value, twii_close, sp500_close)
- 設計為 idempotent：同日重跑會 REPLACE，不會重複
- 在 update_prices.py 跑完後接著跑（盤後 14:30 / 隔日 05:00）
"""
import os
import sys
import sqlite3
from datetime import date

# 沿用 main.py 的 quote helpers，避免重複 TWSE/Yahoo 邏輯
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (
    DATABASE,
    fetch_twse_batch,
    fetch_yahoo_quote,
)


def _get_holdings(db):
    return db.execute("""
        SELECT s.id, s.symbol, s.currency, s.market,
               SUM(CASE WHEN t.action='buy' THEN t.shares
                        WHEN t.action='sell' THEN -t.shares ELSE 0 END) AS shares,
               SUM(CASE WHEN t.action='buy' THEN t.shares*t.price
                        WHEN t.action='sell' THEN -t.shares*t.price ELSE 0 END) AS cost_basis
        FROM stocks s
        JOIN trades t ON s.id = t.stock_id
        GROUP BY s.id
        HAVING shares > 0
    """).fetchall()


def _get_cash(db):
    row = db.execute("""
        SELECT COALESCE(SUM(CASE
            WHEN action='deposit' THEN amount
            WHEN action IN ('withdraw','stock_purchase') THEN -amount
            WHEN action='stock_sell' THEN amount
            ELSE 0 END), 0) AS cash FROM cash
    """).fetchone()
    return float(row["cash"] or 0)


def _fetch_prices(holdings):
    """回 {symbol: price} (TWD-equivalent for foreign 之後再算)"""
    tw_syms = [h["symbol"] for h in holdings if h["currency"] != "USD"]
    us_syms = [h["symbol"] for h in holdings if h["currency"] == "USD"]
    prices = {}
    if tw_syms:
        for sym, d in fetch_twse_batch(tw_syms).items():
            if d.get("price"):
                prices[sym] = d["price"]
    for sym in us_syms:
        d = fetch_yahoo_quote(sym)
        if d.get("price"):
            prices[sym] = d["price"]
    return prices


def _fetch_benchmark():
    """^TWII 與 ^GSPC 收盤"""
    twii = fetch_yahoo_quote("^TWII").get("price") or 0
    sp500 = fetch_yahoo_quote("^GSPC").get("price") or 0
    return float(twii), float(sp500)


def _usd_twd():
    """簡單匯率：USDTWD via Yahoo TWD=X"""
    d = fetch_yahoo_quote("TWD=X")
    return float(d.get("price") or 30.0)


def snapshot():
    today = date.today().isoformat()
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        holdings = _get_holdings(conn)
        if not holdings:
            print(f"[{today}] no holdings, skip", file=sys.stderr)
            return
        prices = _fetch_prices(holdings)
        usd_twd = _usd_twd() if any(h["currency"] == "USD" for h in holdings) else 1.0

        equity_value = 0.0
        rows_to_write = []
        for h in holdings:
            sym = h["symbol"]
            price = prices.get(sym, 0)
            shares = float(h["shares"])
            cost_basis = float(h["cost_basis"])
            avg_cost = cost_basis / shares if shares else 0
            fx = usd_twd if h["currency"] == "USD" else 1.0
            mkt_twd = price * shares * fx
            cost_twd = cost_basis * fx  # 交易紀錄價格與 currency 一致
            pnl = mkt_twd - cost_twd
            pnl_pct = round(pnl / cost_twd * 100, 2) if cost_twd else 0
            equity_value += mkt_twd
            rows_to_write.append((h["id"], today, shares, avg_cost, mkt_twd, pnl, pnl_pct))

        cash = _get_cash(conn)
        total = equity_value + cash
        twii, sp500 = _fetch_benchmark()

        cur = conn.cursor()
        for row in rows_to_write:
            cur.execute("""
                INSERT INTO positions (stock_id, date, shares, avg_cost, market_value, pnl, pnl_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_id, date) DO UPDATE SET
                    shares=excluded.shares,
                    avg_cost=excluded.avg_cost,
                    market_value=excluded.market_value,
                    pnl=excluded.pnl,
                    pnl_pct=excluded.pnl_pct
            """, row)
        cur.execute("""
            INSERT INTO nav_history (date, total_value, cash, equity_value, twii_close, sp500_close)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_value=excluded.total_value,
                cash=excluded.cash,
                equity_value=excluded.equity_value,
                twii_close=excluded.twii_close,
                sp500_close=excluded.sp500_close
        """, (today, total, cash, equity_value, twii or None, sp500 or None))
        conn.commit()

        print(f"[{today}] NAV={total:,.0f}  equity={equity_value:,.0f}  cash={cash:,.0f}  "
              f"TWII={twii or '—'}  SP500={sp500 or '—'}  positions={len(rows_to_write)}")
    finally:
        conn.close()


if __name__ == "__main__":
    snapshot()
