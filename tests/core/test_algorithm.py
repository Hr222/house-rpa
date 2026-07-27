# -*- coding: utf-8 -*-
"""算法单元测试。"""
from app.core.algorithm import (
    aggregate_default_quote,
    AlgorithmInput,
    aggregate_quote_only_prices,
    aggregate_weighted_median_quote,
    diagnose_weighted_median_quote,
    decide,
    decide_quote_only,
    decide_weighted_median,
    evaluate_algorithm,
    find_weighted_price_candidates,
    get_algorithm_strategy,
    mean,
    median,
    remove_extreme_prices,
)

# ============ mean ============


class TestMean:
    def test_simple(self):
        assert mean([100, 200]) == 150.0

    def test_skips_none_and_zero(self):
        assert mean([None, 0, 300]) == 300.0

    def test_empty(self):
        assert mean([]) is None


class TestMedian:
    def test_even_and_odd_lengths(self):
        assert median([100, 300, 200]) == 200.0
        assert median([100, 200, 300, 400]) == 250.0

    def test_deduplicates_prices(self):
        assert median([100, 100, 200, 300]) == 200.0

    def test_removes_extreme_high_and_low_prices(self):
        assert remove_extreme_prices([100, 200, 300, 1000]) == [100.0, 200.0, 300.0]
        assert remove_extreme_prices([1, 100, 101, 102, 103]) == [100.0, 101.0, 102.0, 103.0]
        assert median([100, 200, 300, 1000]) == 200.0

    def test_keeps_values_when_no_extreme_exists(self):
        prices = [100, 200, 300]
        assert remove_extreme_prices(prices) == [100.0, 200.0, 300.0]
        assert median(prices) == 200.0

    def test_empty(self):
        assert median([]) is None


def test_aggregate_quote_only_pools_all_listing_prices():
    assert aggregate_quote_only_prices([[100.0, 200.0, 200.0], [300.0, 1000.0]]) == 200.0


def test_weighted_median_quote_aggregates_listing_prices_across_platforms():
    assert aggregate_weighted_median_quote(
        [[50000.0, 51000.0, 52000.0], [50500.0, 51500.0], [80000.0]],
    ) == 51000.0


def test_weighted_median_quote_returns_none_for_separated_50_50_modes():
    assert aggregate_weighted_median_quote([[50000.0], [80000.0]]) is None


def test_weighted_median_quote_keeps_close_pair_and_ignores_far_outlier():
    assert aggregate_weighted_median_quote([[100.0, 105.0, 300.0]]) == 102.5


def test_weighted_median_quote_accepts_prices_within_ten_percent_of_cluster_center():
    assert aggregate_weighted_median_quote([[100.0], [90.0]]) == 95.0


def test_weighted_median_quote_rejects_prices_far_from_cluster_center():
    assert aggregate_weighted_median_quote([[100.0], [200.0]]) is None


def test_weighted_median_diagnostic_explains_cluster_below_coverage():
    diagnostic = diagnose_weighted_median_quote(
        [[128571.0], [107052.0, 141243.0], [71837.0], [126836.0, 155634.0]],
    )

    assert diagnostic is not None
    assert diagnostic.prices == (126836.0, 128571.0, 141243.0)
    assert diagnostic.coverage == 0.5
    assert diagnostic.max_relative_deviation < 0.10


def test_weighted_median_quote_uses_all_listing_frequency():
    assert aggregate_weighted_median_quote(
        [[50000.0] * 100, [80000.0]],
    ) == 50000.0


def test_weighted_median_keeps_frequent_low_price_and_discards_rare_modes():
    candidates = find_weighted_price_candidates(
        [[20000.0] * 3, [30000.0] * 8, [100000.0] * 2],
    )

    assert [(item.quote_price, item.count) for item in candidates] == [
        (30000.0, 8),
    ]


def test_weighted_median_returns_both_similar_frequency_modes():
    candidates = find_weighted_price_candidates(
        [[20000.0] * 6, [40000.0] * 6],
    )

    assert [(item.quote_price, item.final_price, item.count) for item in candidates] == [
        (20000.0, 18000.0, 6),
        (40000.0, 36000.0, 6),
    ]


def test_algorithm_registry_dispatches_weighted_median_strategy():
    inputs = AlgorithmInput(
        quote_price_lists=[
            [50000.0, 51000.0, 52000.0],
            [50500.0, 51500.0],
            [80000.0],
        ],
        community_avg_prices=[90000.0, 90000.0, 90000.0],
        deal_price_lists=[[40000.0], [40000.0], [40000.0]],
    )

    strategy = get_algorithm_strategy("weighted_median")
    result = evaluate_algorithm("weighted_median", inputs)

    assert strategy.__class__.__name__ == "WeightedMedianAlgorithm"
    assert result.quote_avg == 51000.0
    assert result.deal_avg is None
    assert result.decision.final_price == 45900.0
    assert result.decision.branch == "WEIGHTED_MEDIAN"


def test_weighted_median_evaluation_exposes_multiple_candidates():
    result = evaluate_algorithm(
        "weighted_median",
        AlgorithmInput(
            quote_price_lists=[[20000.0] * 6, [40000.0] * 6],
            community_avg_prices=[None, None],
            deal_price_lists=[[], []],
        ),
    )

    assert result.quote_avg == 20000.0
    assert result.decision.final_price == 20000.0
    assert result.decision.branch == "WEIGHTED_MEDIAN_MULTI"
    assert [candidate.quote_price for candidate in result.candidates] == [
        20000.0,
        40000.0,
    ]
    assert [candidate.final_price for candidate in result.candidates] == [
        20000.0,
        40000.0,
    ]


def test_weighted_median_multi_peak_returns_lowest_peak_without_discount():
    result = evaluate_algorithm(
        "weighted_median",
        AlgorithmInput(
            quote_price_lists=[[100000.0] * 6, [150000.0] * 6, [200000.0] * 6],
            community_avg_prices=[None, None, None],
            deal_price_lists=[[], [], []],
        ),
    )

    assert result.quote_avg == 100000.0
    assert result.decision.final_price == 100000.0
    assert result.decision.branch == "WEIGHTED_MEDIAN_MULTI"


def test_default_quote_keeps_original_mean_behavior():
    assert aggregate_default_quote(
        [[], [], []],
        [100.0, 200.0, 1000.0],
    ) == (100.0 + 200.0 + 1000.0) / 3


def test_algorithm_registry_dispatches_quote_only_strategy():
    inputs = AlgorithmInput(
        quote_price_lists=[[100.0, 200.0, 200.0], [300.0, 1000.0]],
        community_avg_prices=[1000.0, 2000.0],
        deal_price_lists=[[80.0], [150.0]],
    )

    strategy = get_algorithm_strategy("quote_only")
    result = evaluate_algorithm("quote_only", inputs)

    assert strategy.__class__.__name__ == "QuoteOnlyAlgorithm"
    assert result.quote_avg == 200.0
    assert result.deal_avg is None
    assert result.decision.final_price == 180.0


def test_unknown_algorithm_mode_falls_back_to_default():
    inputs = AlgorithmInput(
        quote_price_lists=[[]],
        community_avg_prices=[100.0],
        deal_price_lists=[[90.0]],
    )

    result = evaluate_algorithm("unknown", inputs)

    assert result.quote_avg == 100.0
    assert result.deal_avg == 90.0
    assert result.decision.branch == "DEAL_ONLY"


# ============ decide ============


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


# ============ decide_quote_only ============


class TestDecideQuoteOnly:
    def test_has_quote_with_default_discount(self):
        d = decide_quote_only(quote_avg=100)
        assert d.final_price == 90.0
        assert d.branch == "QUOTE_ONLY"

    def test_has_quote_with_custom_discount(self):
        d = decide_quote_only(quote_avg=100, quote_discount=0.85)
        assert d.final_price == 85.0
        assert d.branch == "QUOTE_ONLY"

    def test_no_quote(self):
        d = decide_quote_only(quote_avg=None)
        assert d.final_price is None
        assert d.branch == "FAILED"


class TestDecideWeightedMedian:
    def test_has_quote_with_default_discount(self):
        d = decide_weighted_median(quote_avg=100)
        assert d.final_price == 90.0
        assert d.branch == "WEIGHTED_MEDIAN"

    def test_no_quote(self):
        d = decide_weighted_median(quote_avg=None)
        assert d.final_price is None
        assert d.branch == "FAILED"
