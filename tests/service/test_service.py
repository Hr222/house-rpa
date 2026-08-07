# -*- coding: utf-8 -*-

import asyncio

import app.service as service_module
from app.core.models import InquiryRequest, ListingSnapshot, PlatformResult
from app.service import RPAInquiryService, build_inquiry_result


def test_build_inquiry_result_averages_listing_and_deal_results():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="平台A",
                status="SUCCESS",
                community_avg_price=90000.0,
                quote_prices=[50000.0, 51000.0, 52000.0],
                deal_prices=[40000.0],
            ),
            PlatformResult(
                name="平台B",
                status="SUCCESS",
                community_avg_price=90000.0,
                quote_prices=[50500.0, 51500.0],
                deal_prices=[40000.0],
            ),
            PlatformResult(
                name="平台C",
                status="SUCCESS",
                quote_prices=[80000.0],
                deal_prices=[40000.0],
            ),
        ]
    )

    assert result.success is True
    assert result.quote_avg == 51000.0
    assert result.deal_avg == 40000.0
    assert result.final_price == 45500.0
    assert result.branch == "WEIGHTED_MEDIAN_COMBINED"


def test_build_inquiry_result_ignores_compatibility_deal_substitutes():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="安居客",
                status="SUCCESS",
                quote_prices=[50000.0],
                deal_prices=[60000.0],
                deal_source="挂牌均价顶替",
            )
        ]
    )

    assert result.quote_avg == 50000.0
    assert result.deal_avg is None
    assert result.final_price == 45000.0
    assert result.branch == "WEIGHTED_MEDIAN"


def test_build_inquiry_result_deduplicates_cross_platform_listings():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="平台A",
                status="SUCCESS",
                quote_prices=[50000.0, 61000.0],
                listing_snapshots=[
                    ListingSnapshot(
                        house_id="a-low",
                        community_name="target",
                        title="low",
                        area=100.0,
                        layout="3 rooms 2 halls",
                        unit_price=50000.0,
                        total_price=500.0,
                    ),
                    ListingSnapshot(
                        house_id="a-high",
                        community_name="target",
                        title="high",
                        area=100.0,
                        layout="3 rooms 2 halls",
                        unit_price=61000.0,
                        total_price=610.0,
                    ),
                ],
            ),
            PlatformResult(
                name="平台B",
                status="SUCCESS",
                quote_prices=[50000.0],
                listing_snapshots=[
                    ListingSnapshot(
                        house_id="b-low",
                        community_name="target",
                        title="low",
                        area=100.0,
                        layout="3 rooms 2 halls",
                        unit_price=50000.0,
                        total_price=500.0,
                    )
                ],
            ),
        ]
    )

    assert result.quote_avg == 50000.0
    assert result.final_price == 50000.0
    assert result.branch == "WEIGHTED_MEDIAN_MULTI"


def test_build_inquiry_result_returns_no_data_when_all_error():
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
    result = build_inquiry_result(
        [
            PlatformResult(
                name="平台B",
                status="SUCCESS",
                quote_prices=[100.126],
                deal_prices=[90.124, 90.126],
            )
        ]
    )

    assert result.quote_avg == 100.13
    assert result.deal_avg == 90.12
    assert result.final_price == 95.13


def test_build_inquiry_result_aggregates_weak_area_reference():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="平台A",
                status="SUCCESS",
                quote_prices=[50000.0],
                reference_code="WEAK_AREA_REFERENCE",
                reference_area_tolerance=10.0,
                reference_area_min=90.0,
                reference_area_max=110.0,
                reference_listing_count=1,
            )
        ]
    )

    assert result.reference_code == "WEAK_AREA_REFERENCE"
    assert result.reference_area_tolerance == 10.0
    assert result.reference_area_min == 90.0
    assert result.reference_area_max == 110.0
    assert result.reference_listing_count == 1


def test_build_inquiry_result_marks_strict_single_listing_as_weak_reference():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="单条平台",
                status="SUCCESS",
                quote_prices=[50000.0],
                listing_snapshots=[
                    ListingSnapshot(
                        house_id="single",
                        community_name="target",
                        area=100.0,
                        unit_price=50000.0,
                    )
                ],
                reference_code="WEAK_AREA_REFERENCE",
                reference_area_tolerance=1.0,
                reference_area_min=99.0,
                reference_area_max=101.0,
                reference_listing_count=1,
            )
        ]
    )

    assert result.final_price == 45000.0
    assert result.reference_code == "WEAK_AREA_REFERENCE"
    assert result.reference_area_tolerance == 1.0
    assert result.reference_listing_count == 1


def test_build_inquiry_result_only_marks_selected_peak_reference():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="弱参考平台",
                status="SUCCESS",
                quote_prices=[20000.0] * 3,
                reference_code="WEAK_AREA_REFERENCE",
                reference_area_tolerance=3.0,
                reference_area_min=97.0,
                reference_area_max=103.0,
                reference_listing_count=2,
            ),
            PlatformResult(
                name="普通平台",
                status="SUCCESS",
                quote_prices=[40000.0] * 3,
            ),
        ]
    )

    assert result.final_price == 20000.0
    assert result.reference_code == "WEAK_AREA_REFERENCE"
    assert result.reference_listing_count == 2

    result = build_inquiry_result(
        [
            PlatformResult(
                name="普通平台",
                status="SUCCESS",
                quote_prices=[20000.0] * 3,
            ),
            PlatformResult(
                name="非选中弱参考平台",
                status="SUCCESS",
                quote_prices=[40000.0] * 3,
                reference_code="WEAK_AREA_REFERENCE",
                reference_area_tolerance=3.0,
                reference_area_min=97.0,
                reference_area_max=103.0,
                reference_listing_count=2,
            ),
        ]
    )

    assert result.final_price == 20000.0
    assert result.reference_code is None


def test_build_inquiry_result_returns_multiple_candidates():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="平台A",
                status="SUCCESS",
                quote_prices=[20000.0] * 6,
            ),
            PlatformResult(
                name="平台B",
                status="SUCCESS",
                quote_prices=[40000.0] * 6,
            ),
        ]
    )

    assert result.success is True
    assert result.quote_avg == 20000.0
    assert result.final_price == 20000.0
    assert result.branch == "WEIGHTED_MEDIAN_MULTI"
    assert [candidate.final_price for candidate in result.candidates] == [
        20000.0,
        40000.0,
    ]


def test_build_inquiry_result_distinguishes_area_mismatch():
    result = build_inquiry_result(
        [
            PlatformResult(
                name="平台A",
                status="NO_MATCHING_AREA",
                reason="命中小区但无请求面积±1㎡房源",
            ),
            PlatformResult(
                name="平台B",
                status="NO_MATCHING_AREA",
                reason="命中小区但无请求面积±1㎡房源",
            ),
        ]
    )

    assert result.success is False
    assert result.final_price is None
    assert result.branch == "NO_MATCHING_AREA"


def test_run_inquiry_checks_risk_before_aggregation(monkeypatch):
    events = []

    class FakePage:
        async def activate(self):
            return None

    class FakeAdapter:
        def __init__(self, code):
            self.code = code
            self.name = code

        async def collect(self, browser, session, request):
            events.append(f"collect:{self.code}")
            await asyncio.sleep(0)
            return PlatformResult(
                name=self.name,
                status="SUCCESS",
                quote_prices=[100.0],
                deal_prices=[100.0],
            )

    adapters = [FakeAdapter("a"), FakeAdapter("b")]
    service = RPAInquiryService({"a": object(), "b": object()}, adapters)
    service.sessions = {
        adapter.code: type("Session", (), {"page": FakePage()})()
        for adapter in adapters
    }

    original_build = service_module.build_inquiry_result

    def tracked_build(results):
        events.append("aggregate")
        return original_build(results)

    monkeypatch.setattr(service_module, "build_inquiry_result", tracked_build)

    async def before_aggregate():
        events.append("risk-check")

    asyncio.run(
        service.run_inquiry(
            InquiryRequest(community_name="小区", area=80),
            before_aggregate=before_aggregate,
        )
    )

    assert events[:2] == ["collect:a", "collect:b"] or events[:2] == [
        "collect:b",
        "collect:a",
    ]
    assert events[-2:] == ["risk-check", "aggregate"]
