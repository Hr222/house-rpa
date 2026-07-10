# -*- coding: utf-8 -*-
"""算法单元测试。"""
from app.algorithm import mean, decide


class TestMean:
    def test_simple(self):
        assert mean([100, 200]) == 150.0

    def test_skips_none_and_zero(self):
        assert mean([None, 0, 300]) == 300.0

    def test_empty(self):
        assert mean([]) is None


class TestDecide:
    def test_diff_within_10pct_take_lower(self):
        d = decide(quote_avg=100, deal_avg=105)  # diff 4.76%
        assert d.final_price == 100
        assert d.branch == "TAKE_LOWER"

    def test_diff_over_10pct_deal_only(self):
        d = decide(quote_avg=100, deal_avg=130)  # diff 23%
        assert d.final_price == 130
        assert d.branch == "DEAL_ONLY"

    def test_no_deal_discount(self):
        d = decide(quote_avg=100, deal_avg=None)
        assert d.final_price == 90.0
        assert d.branch == "QUOTE_DISCOUNT"

    def test_no_quote_no_deal(self):
        d = decide(quote_avg=None, deal_avg=None)
        assert d.final_price is None
        assert d.branch == "FAILED"

    def test_no_quote_has_deal(self):
        d = decide(quote_avg=None, deal_avg=100)
        assert d.final_price == 100
        assert d.branch == "DEAL_ONLY"
