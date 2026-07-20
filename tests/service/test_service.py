# -*- coding: utf-8 -*-

from app.core.models import PlatformResult
from app.service import build_inquiry_result


def test_build_inquiry_result_averages_all_platforms():
    """所有 SUCCESS 平台累加平均：quote 和 deal 都跨平台合并计算。"""
    a = PlatformResult(
        name="平台A", status="SUCCESS",
        community_avg_price=100.0,
        quote_prices=[],
        deal_prices=[90.0],
    )
    b = PlatformResult(
        name="平台B", status="SUCCESS",
        community_avg_price=200.0,
        quote_prices=[],
        deal_prices=[100.0],
    )
    no_data = PlatformResult(name="平台C", status="SUCCESS")  # 无数据，不参与

    result = build_inquiry_result([a, b, no_data])

    assert result.success is True
    # quote_avg = (100+200)/2 = 150
    assert result.quote_avg == 150.0
    # deal_avg = mean([90, 100]) = 95.0
    assert result.deal_avg == 95.0
    # diff = |150-95|/95 = 57.9% > 10% → DEAL_ONLY → 95.0
    assert result.final_price == 95.0
    assert result.branch == "DEAL_ONLY"


def test_build_inquiry_result_returns_failed_when_all_error():
    result = build_inquiry_result(
        [
            PlatformResult(name="平台A", status="ERROR"),
            PlatformResult(name="平台B", status="ERROR"),
        ]
    )

    assert result.success is False
    assert result.final_price is None
    assert result.branch == "NO_DATA"


def test_build_inquiry_result_rounds_prices_to_2_decimals():
    success = PlatformResult(
        name="平台B",
        status="SUCCESS",
        community_avg_price=100.126,
        deal_prices=[90.124, 90.126],
    )

    result = build_inquiry_result([success])

    assert result.quote_avg == 100.13
    assert result.deal_avg == 90.12
    assert result.final_price == 90.12


def test_build_inquiry_result_quote_only():
    """quote_only 模式：只看在售均价打折，忽略成交数据。"""
    a = PlatformResult(
        name="平台A", status="SUCCESS",
        community_avg_price=100.0,
        quote_prices=[],
        deal_prices=[80.0],  # 成交价应被忽略
    )
    b = PlatformResult(
        name="平台B", status="SUCCESS",
        community_avg_price=200.0,
        quote_prices=[],
        deal_prices=[150.0],
    )

    result = build_inquiry_result([a, b], algorithm_mode="quote_only")

    assert result.success is True
    # quote_avg = (100+200)/2 = 150, deal_avg 不使用
    assert result.quote_avg == 150.0
    assert result.deal_avg is None
    # 150 * 0.9 = 135.0
    assert result.final_price == 135.0
    assert result.branch == "QUOTE_ONLY"


def test_build_inquiry_result_quote_only_no_quote():
    """quote_only 模式无在售数据时返回 FAILED。"""
    result = build_inquiry_result(
        [PlatformResult(name="平台A", status="SUCCESS")],
        algorithm_mode="quote_only",
    )

    assert result.success is False
    assert result.final_price is None
    assert result.branch == "NO_DATA"
