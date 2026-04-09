"""手動驗證 FinMind 資料源：python3 scripts/test_valuation_source.py 2330 2886 00878"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import (
    fetch_finmind_financial_statements,
    fetch_finmind_dividend,
    fetch_finmind_per_history,
    fetch_finmind,
)
from valuation import (
    calc_pe_percentiles,
    calc_pe_valuation,
    calc_yield_valuation,
    estimate_forward_eps,
    average_dividend,
    classify_category,
    classify_price,
)
from datetime import date, timedelta


def run(symbol: str):
    print(f"\n═══════════ {symbol} ═══════════")
    eps = fetch_finmind_financial_statements(symbol, quarters=8)
    divs = fetch_finmind_dividend(symbol, years=5)
    per_hist, pbr_hist, _latest = fetch_finmind_per_history(symbol, days=365 * 5)

    print(f"季 EPS ({len(eps)}): {eps}")
    print(f"近 5 年股利 ({len(divs)}): {divs}")
    print(f"歷史 PE 筆數: {len(per_hist)}")

    # current price
    today = date.today().isoformat()
    start = (date.today() - timedelta(days=10)).isoformat()
    try:
        raw = fetch_finmind("TaiwanStockPrice", symbol, start, today)
        current = float(raw["data"][-1]["close"]) if raw.get("data") else None
    except Exception as e:
        current = None
        print(f"  current price error: {e}")
    print(f"當前價: {current}")

    # 分類
    try:
        info_raw = fetch_finmind("TaiwanStockInfo", symbol, "", "")
        industry = (info_raw.get("data") or [{}])[0].get("industry_category", "")
    except Exception:
        industry = ""
    category, method = classify_category(symbol, industry, None)
    print(f"產業: {industry!r} → 分類: {category} ({method} 法)")

    # 估價
    if method == "pe":
        fwd, growth = estimate_forward_eps(eps)
        pe_low, pe_mid, pe_high = calc_pe_percentiles(per_hist)
        print(f"前瞻 EPS: {fwd} (成長 {growth})   PE 區間: {pe_low}/{pe_mid}/{pe_high}")
        val = calc_pe_valuation(fwd, pe_low, pe_mid, pe_high)
    else:
        avg_d = average_dividend(divs, years=5)
        print(f"平均股利: {avg_d}")
        val = calc_yield_valuation(avg_d, category)

    tag = classify_price(current, val["cheap"], val["fair"], val["expensive"])
    print(f"三值估價: 便宜 {val['cheap']} / 合理 {val['fair']} / 昂貴 {val['expensive']}")
    print(f"標籤: {tag}")


if __name__ == "__main__":
    symbols = sys.argv[1:] or ["2330", "2886", "00878"]
    for s in symbols:
        try:
            run(s)
        except Exception as e:
            print(f"{s} ERROR: {e}")
