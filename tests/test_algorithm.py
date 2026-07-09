# -*- coding: utf-8 -*-
import pytest
from app.algorithm import filter_by_area, mean_price, decide_final_price


class TestFilterByArea:
    def test_keeps_within_range(self):
        listings = [{"area": 100}, {"area": 110}, {"area": 90}]
        result = filter_by_area(listings, area_min=80, area_max=120)
        assert len(result) == 3

    def test_filters_outside_range(self):
        listings = [{"area": 70}, {"area": 100}, {"area": 130}]
        result = filter_by_area(listings, area_min=80, area_max=120)
        assert len(result) == 1
        assert result[0]["area"] == 100

    def test_boundary_inclusive(self):
        # 80 和 120 是边界，应保留
        listings = [{"area": 80}, {"area": 120}]
        result = filter_by_area(listings, area_min=80, area_max=120)
        assert len(result) == 2

    def test_none_area_excluded(self):
        listings = [{"area": None}, {"area": 100}]
        result = filter_by_area(listings, area_min=80, area_max=120)
        assert len(result) == 1


class TestMeanPrice:
    def test_simple_mean(self):
        assert mean_price([{"unit_price": 100}, {"unit_price": 200}]) == 150.0

    def test_skips_none(self):
        assert mean_price([{"unit_price": None}, {"unit_price": 300}]) == 300.0

    def test_empty_returns_none(self):
        assert mean_price([]) is None


class TestDecideFinalPrice:
    def test_diff_within_10pct_take_lower(self):
        # quote=100, deal=105, diff=4.76% <=10% → min=100
        result = decide_final_price(quote_avg=100, deal_avg=105)
        assert result.final_price == 100
        assert result.branch == "TAKE_LOWER"

    def test_diff_over_10pct_take_deal_only(self):
        # quote=100, deal=130, diff=23% >10% → deal=130
        result = decide_final_price(quote_avg=100, deal_avg=130)
        assert result.final_price == 130
        assert result.branch == "DEAL_ONLY"

    def test_no_deal_discount_quote(self):
        # 无成交 → 100 * 0.8 = 80
        result = decide_final_price(quote_avg=100, deal_avg=None)
        assert result.final_price == 80.0
        assert result.branch == "QUOTE_DISCOUNT"

    def test_no_quote_no_deal_failed(self):
        result = decide_final_price(quote_avg=None, deal_avg=None)
        assert result.final_price is None
        assert result.branch == "FAILED"

    def test_no_quote_but_has_deal(self):
        # 无报价但有成交 → 取成交价
        result = decide_final_price(quote_avg=None, deal_avg=100)
        assert result.final_price == 100
        assert result.branch == "DEAL_ONLY"
