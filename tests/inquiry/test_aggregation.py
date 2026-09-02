# -*- coding: utf-8 -*-
"""询价聚合保持在 RPA 采集边界之上。"""

from __future__ import annotations

from app.inquiry.aggregation import build_inquiry_result
from app.rpa.core.models import ListingSnapshot, PlatformResult
from app.rpa.core.status import PlatformResultStatus


def test_aggregation_uses_raw_snapshots_and_returns_final_price(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.inquiry.aggregation.get_weighted_median_discount",
        lambda: 0.9,
    )
    raw_result = PlatformResult(
        name="测试平台",
        status=PlatformResultStatus.SUCCESS,
        listing_snapshots=[
            ListingSnapshot(
                house_id="house-1",
                community_name="示例花园一期",
                area=89.5,
                unit_price=100000.0,
                total_price=890.0,
            )
        ],
    )

    result = build_inquiry_result([raw_result], request_area=89.5)

    assert result.success is True
    assert result.quote_avg == 100000.0
    assert result.final_price == 90000.0
    assert result.platform_results == [raw_result]


def test_aggregation_applies_weak_reference_after_rpa_collection(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.inquiry.aggregation.get_weighted_median_discount",
        lambda: 0.9,
    )
    raw_result = PlatformResult(
        name="测试平台",
        status=PlatformResultStatus.SUCCESS,
        listing_snapshots=[
            ListingSnapshot(
                house_id="house-2",
                community_name="示例花园一期",
                area=109.0,
                unit_price=100000.0,
                total_price=1090.0,
            )
        ],
    )

    result = build_inquiry_result([raw_result], request_area=89.5)

    assert result.success is True
    assert result.reference_code == "WEAK_AREA_REFERENCE"
    assert result.reference_area_tolerance == 19.5
    assert result.reference_listing_count == 1
