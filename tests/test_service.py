# -*- coding: utf-8 -*-

from app.models import PlatformResult
from app.service import build_inquiry_result


def test_build_inquiry_result_prefers_first_success():
    blocked = PlatformResult(name="平台A", status="WAIT_MANUAL_VERIFY")
    success = PlatformResult(
        name="平台B",
        status="SUCCESS",
        community_avg_price=100.0,
        deal_prices=[95.0],
    )

    result = build_inquiry_result([blocked, success])

    assert result.success is True
    assert result.final_price == 95.0
    assert result.platform is success
    assert result.platform_results == [blocked, success]


def test_build_inquiry_result_returns_failed_when_all_error():
    result = build_inquiry_result(
        [
            PlatformResult(name="平台A", status="ERROR"),
            PlatformResult(name="平台B", status="ERROR"),
        ]
    )

    assert result.success is False
    assert result.final_price is None
    assert result.branch == "FAILED"
