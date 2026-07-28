# -*- coding: utf-8 -*-
"""加权落点中位数算法测试。"""

from app.core.algorithm import (
    ALGORITHM_REGISTRY,
    AlgorithmInput,
    aggregate_weighted_median_quote,
    decide_weighted_median,
    diagnose_weighted_median_quote,
    evaluate_algorithm,
    find_weighted_price_candidates,
    get_algorithm_strategy,
)


def test_algorithm_registry_preserves_extension_point_with_default_only():
    assert set(ALGORITHM_REGISTRY) == {"DEFAULT"}
    assert get_algorithm_strategy() is get_algorithm_strategy("default")
    assert get_algorithm_strategy("future_mode") is get_algorithm_strategy("DEFAULT")


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


def test_weighted_median_evaluation_exposes_single_candidate():
    result = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=[
                [50000.0, 51000.0, 52000.0],
                [50500.0, 51500.0],
                [80000.0],
            ],
        ),
    )

    assert result.quote_avg == 51000.0
    assert result.deal_avg is None
    assert result.decision.final_price == 45900.0
    assert result.decision.branch == "WEIGHTED_MEDIAN"


def test_weighted_median_evaluation_exposes_multiple_candidates():
    result = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=[[20000.0] * 6, [40000.0] * 6],
        ),
    )

    assert result.quote_avg == 20000.0
    assert result.decision.final_price == 20000.0
    assert result.decision.branch == "WEIGHTED_MEDIAN_MULTI"
    assert [candidate.quote_price for candidate in result.candidates] == [
        20000.0,
        40000.0,
    ]


def test_weighted_median_combines_listing_and_deal_results_by_equal_average():
    result = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=[[50000.0]],
            deal_price_lists=[[40000.0]],
        )
    )

    assert result.quote_avg == 50000.0
    assert result.deal_avg == 40000.0
    assert result.decision.final_price == 45000.0
    assert result.decision.branch == "WEIGHTED_MEDIAN_COMBINED"


def test_weighted_median_uses_deal_price_mode_without_listing_discount():
    result = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=[[50000.0]],
            deal_price_lists=[[40000.0, 41000.0, 80000.0]],
        )
    )

    assert result.deal_avg == 40500.0
    assert result.decision.final_price == 45250.0


def test_weighted_median_multi_peak_returns_lowest_peak_without_discount():
    result = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=[[100000.0] * 6, [150000.0] * 6, [200000.0] * 6],
        ),
    )

    assert result.quote_avg == 100000.0
    assert result.decision.final_price == 100000.0
    assert result.decision.branch == "WEIGHTED_MEDIAN_MULTI"


def test_weighted_median_decision_returns_discounted_single_peak():
    decision = decide_weighted_median(100.0)
    assert decision.final_price == 90.0
    assert decision.branch == "WEIGHTED_MEDIAN"


def test_weighted_median_decision_fails_without_peak():
    decision = decide_weighted_median(None)
    assert decision.final_price is None
    assert decision.branch == "FAILED"
