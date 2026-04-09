"""三值估價引擎 — 純函式，無 I/O。

提供：
- calc_pe_valuation: 本益比法
- calc_yield_valuation: 殖利率法（存股 / 金融 / ETF）
- calc_pe_percentiles: 個股歷史 PE 分位數
- estimate_forward_eps: 由歷史季 EPS 推估前瞻 EPS
- classify_price: 把當前股價歸類到 cheap/fair/expensive
- classify_category: 依產業/代號判斷分類與對應估價法
"""

from __future__ import annotations

from statistics import median
from typing import Literal, Optional


Tag = Literal["cheap", "fair", "expensive"]
Category = Literal["低估成長股", "金融股", "景氣循環股", "ETF"]
Method = Literal["pe", "yield"]


# ─── 分類設定 ───────────────────────────────────────────

_FINANCIAL_INDUSTRIES = {"金融保險", "金融保險業", "金控", "銀行"}
_CYCLICAL_INDUSTRIES = {"鋼鐵工業", "航運業", "塑膠工業", "水泥工業", "紡織纖維", "鋼鐵", "航運", "塑膠", "水泥", "紡織"}

# 殖利率法倍數：便宜 / 合理 / 昂貴
_YIELD_MULTIPLIERS = {
    "金融股": (16.0, 20.0, 25.0),
    "ETF": (18.0, 22.0, 28.0),
    "低估成長股": (16.0, 20.0, 32.0),
    "景氣循環股": (16.0, 20.0, 32.0),
}


def classify_category(symbol: str, industry: str | None, stock_type: str | None = None) -> tuple[Category, Method]:
    """依 symbol/industry 回傳 (分類, 建議估價法)。"""
    industry = (industry or "").strip()
    symbol = (symbol or "").strip()

    # ETF：台股 ETF 代號 00 開頭 4-6 碼
    if symbol.startswith("00") and len(symbol) >= 4:
        return "ETF", "yield"

    if industry in _FINANCIAL_INDUSTRIES or "金融" in industry or "銀行" in industry:
        return "金融股", "yield"

    if industry in _CYCLICAL_INDUSTRIES or any(k in industry for k in ("鋼鐵", "航運", "塑膠", "水泥", "紡織")):
        return "景氣循環股", "pe"

    return "低估成長股", "pe"


# ─── PE 法 ──────────────────────────────────────────────

def calc_pe_valuation(eps: float, pe_low: float, pe_mid: float, pe_high: float) -> dict:
    """本益比法三值估價。EPS <= 0 時回傳 None 價格。"""
    if eps is None or eps <= 0:
        return {"cheap": None, "fair": None, "expensive": None}
    return {
        "cheap": round(eps * pe_low, 2),
        "fair": round(eps * pe_mid, 2),
        "expensive": round(eps * pe_high, 2),
    }


def _percentiles(values: list[float], qs: tuple[float, float, float]) -> tuple[float, float, float]:
    clean = sorted(v for v in values if v is not None and v > 0 and v < 10000)
    n = len(clean)
    def q(p: float) -> float:
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return clean[lo] * (1 - frac) + clean[hi] * frac
    return round(q(qs[0]), 2), round(q(qs[1]), 2), round(q(qs[2]), 2)


from statistics import median as _median


def _median_band(values: list[float], max_val: float, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    """中位數 ± 20% 區間。資料 < 30 筆時 fallback。"""
    clean = [p for p in (values or []) if p is not None and p > 0 and p < max_val]
    if len(clean) < 30:
        return fallback
    mid = _median(clean)
    return round(mid * 0.8, 2), round(mid, 2), round(mid * 1.2, 2)


def calc_pe_percentiles(historical_pe: list[float], *, fallback: tuple[float, float, float] = (10.0, 15.0, 20.0)) -> tuple[float, float, float]:
    """PE 中位數 ± 20%（配合 3 年窗口呼叫）。"""
    return _median_band(historical_pe, 200, fallback)


def calc_pbr_percentiles(historical_pbr: list[float], *, fallback: tuple[float, float, float] = (1.0, 1.5, 2.0)) -> tuple[float, float, float]:
    """PBR 中位數 ± 20%（配合 3 年窗口呼叫）。"""
    return _median_band(historical_pbr, 50, fallback)


def calc_pbr_valuation(bps: float, pbr_low: float, pbr_mid: float, pbr_high: float) -> dict:
    """股淨比法三值估價。BPS <= 0 回 None。"""
    if bps is None or bps <= 0:
        return {"cheap": None, "fair": None, "expensive": None}
    return {
        "cheap": round(bps * pbr_low, 2),
        "fair": round(bps * pbr_mid, 2),
        "expensive": round(bps * pbr_high, 2),
    }


def estimate_forward_eps(quarterly_eps: list[float]) -> tuple[Optional[float], Optional[float]]:
    """由最近 8 季 EPS 推估 (TTM, 前瞻 EPS, YoY 成長率)。

    回傳 (forward_eps, growth_rate)。資料不足時回傳 (None, None) 或 (TTM, None)。
    """
    clean = [e for e in (quarterly_eps or []) if e is not None]
    if len(clean) < 4:
        return None, None
    ttm = sum(clean[-4:])
    if len(clean) < 8:
        return round(ttm, 2), None
    prev_ttm = sum(clean[-8:-4])
    if prev_ttm <= 0:
        return round(ttm, 2), None
    growth = (ttm - prev_ttm) / prev_ttm
    # Clamp 避免極端值
    growth = max(-0.5, min(1.0, growth))
    forward = ttm * (1 + growth)
    return round(forward, 2), round(growth, 4)


# ─── 殖利率法 ───────────────────────────────────────────

def calc_yield_valuation(avg_dividend: float, category: Category = "金融股") -> dict:
    """殖利率法：便宜/合理/昂貴 = 股利 × 倍數區間。"""
    if avg_dividend is None or avg_dividend <= 0:
        return {"cheap": None, "fair": None, "expensive": None}
    low, mid, high = _YIELD_MULTIPLIERS.get(category, (16.0, 20.0, 32.0))
    return {
        "cheap": round(avg_dividend * low, 2),
        "fair": round(avg_dividend * mid, 2),
        "expensive": round(avg_dividend * high, 2),
    }


def average_dividend(dividends: list[float], *, years: int = 5) -> Optional[float]:
    """近 N 年現金股利平均。資料不足時取可得年份平均。"""
    clean = [d for d in (dividends or []) if d is not None and d >= 0]
    if not clean:
        return None
    window = clean[-years:] if len(clean) >= years else clean
    return round(sum(window) / len(window), 3)


# ─── 分類標籤 ───────────────────────────────────────────

def classify_price(current: float, cheap: Optional[float], fair: Optional[float], expensive: Optional[float]) -> Optional[Tag]:
    """歸類當前股價。
    - current < cheap → 'cheap'
    - current < expensive → 'fair'
    - else → 'expensive'
    """
    if current is None or current <= 0:
        return None
    if cheap is None or fair is None or expensive is None:
        return None
    if current < cheap:
        return "cheap"
    if current < expensive:
        return "fair"
    return "expensive"
