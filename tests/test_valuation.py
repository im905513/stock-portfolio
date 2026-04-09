"""Unit tests for valuation.py — pure functions, no I/O."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from valuation import (
    calc_pe_valuation,
    calc_pe_percentiles,
    calc_yield_valuation,
    classify_price,
    classify_category,
    estimate_forward_eps,
    average_dividend,
)


# ─── PE 法 ───
class TestPeValuation:
    def test_basic(self):
        r = calc_pe_valuation(eps=10.0, pe_low=10, pe_mid=15, pe_high=20)
        assert r == {"cheap": 100.0, "fair": 150.0, "expensive": 200.0}

    def test_zero_eps(self):
        assert calc_pe_valuation(0, 10, 15, 20) == {"cheap": None, "fair": None, "expensive": None}

    def test_negative_eps(self):
        assert calc_pe_valuation(-2.5, 10, 15, 20)["cheap"] is None

    def test_none_eps(self):
        assert calc_pe_valuation(None, 10, 15, 20)["fair"] is None


# ─── PE 分位數 ───
class TestPePercentiles:
    def test_insufficient_data_fallback(self):
        assert calc_pe_percentiles([10, 15, 20]) == (10.0, 15.0, 20.0)

    def test_empty(self):
        assert calc_pe_percentiles([]) == (10.0, 15.0, 20.0)

    def test_known_distribution(self):
        # 1..100 uniform → p20=20.8, p50=50.5, p80=80.2
        data = list(range(1, 101))
        low, mid, high = calc_pe_percentiles(data)
        assert 19.5 <= low <= 21.5
        assert 49.5 <= mid <= 51.5
        assert 79.5 <= high <= 81.5

    def test_filters_negative_and_extreme(self):
        data = [-5, 0, 500] + list(range(1, 41))  # 垃圾值應被濾掉
        low, mid, high = calc_pe_percentiles(data)
        assert low > 0
        assert high < 50

    def test_custom_fallback(self):
        assert calc_pe_percentiles([1, 2], fallback=(5, 10, 15)) == (5, 10, 15)


# ─── Forward EPS ───
class TestForwardEps:
    def test_insufficient(self):
        assert estimate_forward_eps([1, 2]) == (None, None)

    def test_only_4q_returns_ttm(self):
        fwd, growth = estimate_forward_eps([1.0, 1.5, 2.0, 2.5])
        assert fwd == 7.0
        assert growth is None

    def test_growth_positive(self):
        # prev 4q sum=4, recent 4q sum=8 → growth = 100% → clamp to 100%
        fwd, growth = estimate_forward_eps([1, 1, 1, 1, 2, 2, 2, 2])
        assert growth == 1.0
        assert fwd == 16.0

    def test_growth_negative_clamped(self):
        # prev=20, recent=5 → -75% → clamp to -50%
        fwd, growth = estimate_forward_eps([5, 5, 5, 5, 1, 1, 1, 2])
        assert growth == -0.5
        assert fwd == 2.5

    def test_prev_zero_fallback_ttm(self):
        fwd, growth = estimate_forward_eps([0, 0, 0, 0, 1, 1, 1, 1])
        assert fwd == 4.0
        assert growth is None


# ─── 殖利率法 ───
class TestYieldValuation:
    def test_financial(self):
        r = calc_yield_valuation(2.0, "金融股")
        assert r == {"cheap": 32.0, "fair": 40.0, "expensive": 50.0}

    def test_etf(self):
        r = calc_yield_valuation(1.5, "ETF")
        assert r == {"cheap": 27.0, "fair": 33.0, "expensive": 42.0}

    def test_zero(self):
        assert calc_yield_valuation(0)["cheap"] is None

    def test_none(self):
        assert calc_yield_valuation(None)["fair"] is None


class TestAverageDividend:
    def test_5y(self):
        assert average_dividend([1, 2, 3, 4, 5]) == 3.0

    def test_more_than_5y_takes_last(self):
        assert average_dividend([10, 10, 1, 2, 3, 4, 5]) == 3.0

    def test_insufficient(self):
        assert average_dividend([2, 4]) == 3.0

    def test_empty(self):
        assert average_dividend([]) is None

    def test_none_filtered(self):
        assert average_dividend([None, 2, 4]) == 3.0


# ─── 分類標籤 ───
class TestClassifyPrice:
    def test_cheap(self):
        assert classify_price(50, 100, 150, 200) == "cheap"

    def test_fair_lower(self):
        assert classify_price(100, 100, 150, 200) == "fair"  # boundary: 等於 cheap 算 fair

    def test_fair_mid(self):
        assert classify_price(175, 100, 150, 200) == "fair"

    def test_expensive(self):
        assert classify_price(250, 100, 150, 200) == "expensive"

    def test_expensive_boundary(self):
        assert classify_price(200, 100, 150, 200) == "expensive"

    def test_none_current(self):
        assert classify_price(None, 100, 150, 200) is None

    def test_none_prices(self):
        assert classify_price(100, None, 150, 200) is None


# ─── 分類判定 ───
class TestClassifyCategory:
    def test_etf(self):
        cat, method = classify_category("0050", "ETF", None)
        assert cat == "ETF"
        assert method == "yield"

    def test_etf_00878(self):
        cat, _ = classify_category("00878", "", None)
        assert cat == "ETF"

    def test_financial(self):
        cat, method = classify_category("2886", "金融保險", None)
        assert cat == "金融股"
        assert method == "yield"

    def test_financial_keyword(self):
        cat, _ = classify_category("2882", "金融保險業", None)
        assert cat == "金融股"

    def test_cyclical_steel(self):
        cat, method = classify_category("2002", "鋼鐵工業", None)
        assert cat == "景氣循環股"
        assert method == "pe"

    def test_cyclical_shipping(self):
        cat, _ = classify_category("2603", "航運業", None)
        assert cat == "景氣循環股"

    def test_growth_default(self):
        cat, method = classify_category("2330", "半導體", None)
        assert cat == "低估成長股"
        assert method == "pe"
