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


def test_weighted_median_keeps_each_dense_price_cluster_as_a_peak():
    candidates = find_weighted_price_candidates(
        [[20000.0] * 3, [30000.0] * 8, [100000.0] * 2],
    )

    assert [(item.quote_price, item.count) for item in candidates] == [
        (20000.0, 3),
        (30000.0, 8),
        (100000.0, 2),
    ]


def test_weighted_median_discards_separate_single_point_outliers():
    candidates = find_weighted_price_candidates(
        [[30000.0] * 8, [10000.0], [100000.0], [200000.0]],
    )

    assert [(item.quote_price, item.count) for item in candidates] == [
        (30000.0, 8),
    ]


def test_weighted_median_uses_lowest_of_uneven_dense_peaks():
    result = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=[
                [61470.0, 62208.0, 62209.0, 62209.0],
                [89442.0, 89480.0, 97261.0, 97277.0, 101167.0, 105042.0, 105043.0],
            ],
        )
    )

    assert [candidate.quote_price for candidate in result.candidates] == [
        62208.5,
        97277.0,
    ]
    assert result.quote_avg == 62208.5
    assert result.decision.final_price == 62208.5
    assert result.decision.branch == "WEIGHTED_MEDIAN_MULTI"


def test_weighted_median_keeps_nearby_dense_low_peak_without_discount():
    result = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=[
                [41889.0, 45526.0, 46821.0, 49451.0],
                [39849.0, 39795.0, 55663.0],
            ],
        )
    )

    assert [(candidate.quote_price, candidate.count) for candidate in result.candidates] == [
        (39849.0, 3),
        (46173.5, 4),
    ]
    assert result.quote_avg == 39849.0
    assert result.decision.final_price == 39849.0
    assert result.decision.branch == "WEIGHTED_MEDIAN_MULTI"


def test_weighted_median_does_not_promote_bridge_window_over_existing_peaks():
    candidates = find_weighted_price_candidates(
        [[
            112359.0, 113612.0, 115444.0, 115730.0, 115731.0, 125630.0,
            126055.0, 133645.0, 134726.0, 134832.0, 140172.0, 143225.0,
            143466.0, 143708.0, 143820.0, 143821.0, 144393.0, 144635.0,
            145708.0, 145822.0, 146052.0, 146067.0, 149438.0, 151261.0,
            151278.0, 151567.0, 152552.0, 154397.0, 154934.0, 155056.0,
            155318.0, 156744.0, 156751.0, 156751.0, 162338.0, 162921.0,
            162940.0, 174177.0,
        ]]
    )

    assert [(candidate.quote_price, candidate.count) for candidate in candidates] == [
        (115730.0, 7),
        (149438.0, 29),
    ]


def test_weighted_median_does_not_restore_overlapping_windows_in_one_price_band():
    candidates = find_weighted_price_candidates(
        [[
            52520.0, 53147.0, 53596.0, 54495.0, 55057.0, 55705.0, 55817.0,
            55860.0, 56180.0, 57478.0, 57925.0, 58049.0, 58288.0, 58607.0,
            59152.0, 59165.0, 59212.0, 59212.0, 59551.0, 59551.0, 59551.0,
            59551.0, 59551.0, 59551.0, 59790.0, 60201.0, 60349.0, 60617.0,
            61174.0, 62500.0, 62563.0, 62570.0, 62584.0, 62801.0, 63143.0,
            63256.0, 63528.0, 64037.0, 64139.0, 64747.0, 64819.0, 65039.0,
            65039.0, 65039.0, 65220.0, 65264.0, 65264.0, 65640.0, 65640.0,
            65640.0, 65783.0, 66164.0, 66422.0, 66920.0, 67288.0, 67290.0,
            69066.0, 69177.0, 70328.0, 70652.0, 70652.0, 71549.0, 72895.0,
            72895.0, 73034.0, 73141.0, 73596.0, 74914.0, 75391.0, 76371.0,
            83818.0, 87769.0, 87769.0,
        ]]
    )

    assert [(candidate.quote_price, candidate.count) for candidate in candidates] == [
        (62584.0, 47),
        (87769.0, 3),
    ]


def test_weighted_median_does_not_duplicate_mode_covered_by_neighboring_peaks():
    candidates = find_weighted_price_candidates(
        [[
            42372.0, 56050.0, 56423.0, 56637.0, 57034.0, 57057.0, 57129.0,
            57780.0, 57822.0, 58135.0, 59411.0, 59710.0, 59895.0, 60341.0,
            63755.0, 65558.0, 66431.0, 71725.0, 75782.0,
        ]]
    )

    assert [(candidate.quote_price, candidate.count) for candidate in candidates] == [
        (57780.0, 13),
        (66431.0, 5),
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
