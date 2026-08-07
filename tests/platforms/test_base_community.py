# -*- coding: utf-8 -*-
"""平台基类小区匹配辅助函数测试。"""

from app.core.models import ListingSnapshot
from app.platforms.base import (
    community_name_match,
    deal_area_bounds,
    filter_snapshots_by_area,
    filter_snapshots_by_area_with_fallback,
    filter_snapshots_by_community,
    has_matching_community_snapshots,
    listing_area_bounds,
    listing_filter_summary,
    listing_no_data_reason,
    listing_no_data_status,
    deduplicate_same_platform,
    prepare_listing_data,
    prepare_listing_data_with_reference,
)


def test_deal_area_bounds_uses_plus_minus_five_without_widening_listing_scope():
    """成交使用 ±5㎡，但不改变在售房源既有的 ±1㎡范围。"""
    assert deal_area_bounds(104.03) == (99.03, 109.03)
    assert listing_area_bounds(104.03) == (103.03, 105.03)


def test_has_matching_community_snapshots_respects_prefix_alias():
    """简称仍可匹配带前缀的小区全名。"""
    snapshots = [
        ListingSnapshot(house_id="1", community_name="华润静安府"),
        ListingSnapshot(house_id="2", community_name="前海丹华"),
    ]

    assert has_matching_community_snapshots(snapshots, "静安府") is True


def test_has_matching_community_snapshots_returns_false_when_none_match():
    """完全没有匹配小区时返回 False。"""
    snapshots = [
        ListingSnapshot(house_id="1", community_name="前海丹华"),
        ListingSnapshot(house_id="2", community_name="半山臻境"),
    ]

    assert has_matching_community_snapshots(snapshots, "静安府") is False


def test_filter_snapshots_uses_community_match_rule():
    """过滤结果复用统一的小区匹配规则。"""
    snapshots = [
        ListingSnapshot(house_id="1", community_name="华润静安府"),
        ListingSnapshot(house_id="2", community_name="静安府"),
        ListingSnapshot(house_id="3", community_name="前海丹华"),
    ]

    filtered = filter_snapshots_by_community(snapshots, "静安府")

    assert [item.community_name for item in filtered] == ["华润静安府", "静安府"]


def test_community_name_match_rejects_shared_prefix_only():
    """共享品牌或产品系前缀不能被猜成同一个小区。"""
    assert community_name_match("示例金域东园", "示例金域香园") is False


def test_filter_uses_captured_community_name_not_listing_title():
    """标题出现目标名称时，结构化小区字段不匹配仍必须剔除。"""
    snapshots = [
        ListingSnapshot(
            house_id="1",
            community_name="其他家园",
            title="示例花园精装房源",
            unit_price=50000.0,
        )
    ]

    assert filter_snapshots_by_community(snapshots, "示例花园") == []


def test_prepare_listing_data_keeps_phase_aliases_and_removes_unrelated():
    """分期差异保留，但完全无关的小区不能进入明细和价格。"""
    snapshots = [
        ListingSnapshot(
            house_id="1", community_name="示例花园一期", unit_price=65000.0
        ),
        ListingSnapshot(
            house_id="2", community_name="其他家园", unit_price=48000.0
        ),
        ListingSnapshot(
            house_id="3", community_name="示例花园二期", unit_price=67000.0
        ),
    ]

    filtered, quote_prices = prepare_listing_data(
        snapshots, "示例花园(四期西区)"
    )

    assert [item.house_id for item in filtered] == ["1", "3"]
    assert quote_prices == [65000.0, 67000.0]


def test_prepare_listing_data_preserves_snapshots_without_house_ids():
    """没有房源 ID 的平台也必须保留全部匹配明细。"""
    snapshots = [
        ListingSnapshot(
            house_id="", community_name="示例花园一期", unit_price=65000.0
        ),
        ListingSnapshot(
            house_id="", community_name="示例花园二期", unit_price=67000.0
        ),
    ]

    filtered, quote_prices = prepare_listing_data(
        snapshots, "示例花园(四期西区)"
    )

    assert len(filtered) == 2
    assert quote_prices == [65000.0, 67000.0]


def test_prepare_listing_data_deduplicates_complete_same_platform_rows():
    snapshots = [
        ListingSnapshot(
            house_id="",
            community_name="target",
            title="same listing",
            area=100.0,
            layout="3 rooms 2 halls",
            unit_price=50000.0,
            total_price=500.0,
        ),
        ListingSnapshot(
            house_id="",
            community_name="target",
            title="same listing",
            area=100.0,
            layout="3 rooms 2 halls",
            unit_price=50000.0,
            total_price=500.0,
        ),
        ListingSnapshot(
            house_id="",
            community_name="target",
            title="different listing",
            area=100.0,
            layout="3 rooms 2 halls",
            unit_price=50000.0,
            total_price=500.0,
        ),
    ]

    filtered, quote_prices = prepare_listing_data(snapshots, "target", 100.0)

    assert len(filtered) == 2
    assert quote_prices == [50000.0, 50000.0]


def test_same_platform_dedup_prefers_stable_house_id():
    snapshots = [
        ListingSnapshot(house_id="house-1", community_name="target", unit_price=50000.0),
        ListingSnapshot(house_id="house-1", community_name="target", unit_price=51000.0),
        ListingSnapshot(house_id="house-2", community_name="target", unit_price=50000.0),
    ]

    deduplicated = deduplicate_same_platform(snapshots)

    assert [item.house_id for item in deduplicated] == ["house-1", "house-2"]
    assert [item.unit_price for item in deduplicated] == [50000.0, 50000.0]


def test_filter_snapshots_by_area_uses_request_area_delta():
    """在售房源按请求面积 ±1㎡ 严格过滤，边界值保留。"""
    snapshots = [
        ListingSnapshot(house_id="low", area=98.9),
        ListingSnapshot(house_id="min", area=99.0),
        ListingSnapshot(house_id="mid", area=100.0),
        ListingSnapshot(house_id="max", area=101.0),
        ListingSnapshot(house_id="high", area=101.1),
        ListingSnapshot(house_id="unknown", area=None),
    ]

    assert listing_area_bounds(100.0) == (99.0, 101.0)
    filtered = filter_snapshots_by_area(snapshots, 100.0)

    assert [item.house_id for item in filtered] == ["min", "mid", "max"]


def test_filter_snapshots_by_area_keeps_strict_single_listing_for_weak_reference():
    snapshots = [
        ListingSnapshot(house_id="strict", area=100.0, unit_price=50000.0),
        ListingSnapshot(house_id="near", area=102.0, unit_price=50000.0),
        ListingSnapshot(house_id="farthest", area=103.0, unit_price=50000.0),
        ListingSnapshot(house_id="outside", area=111.0, unit_price=50000.0),
    ]

    filtered, applied_tolerance = filter_snapshots_by_area_with_fallback(
        snapshots,
        100.0,
    )

    assert [item.house_id for item in filtered] == ["strict"]
    assert applied_tolerance == 1.0


def test_prepare_listing_data_marks_weak_area_reference():
    snapshots = [
        ListingSnapshot(
            house_id="near-1",
            community_name="target",
            area=102.0,
            unit_price=50000.0,
        ),
        ListingSnapshot(
            house_id="near-2",
            community_name="target",
            area=102.0,
            unit_price=50000.0,
        ),
        ListingSnapshot(
            house_id="farthest",
            community_name="target",
            area=103.0,
            unit_price=50000.0,
        ),
    ]

    filtered, quote_prices, reference = prepare_listing_data_with_reference(
        snapshots,
        "target",
        100.0,
    )

    assert [item.house_id for item in filtered] == ["near-1", "near-2", "farthest"]
    assert quote_prices == [50000.0, 50000.0, 50000.0]
    assert reference == {
        "reference_code": "WEAK_AREA_REFERENCE",
        "reference_area_tolerance": 3.0,
        "reference_area_min": 97.0,
        "reference_area_max": 103.0,
        "reference_listing_count": 3,
    }


def test_strict_multiple_listings_remain_in_strict_range():
    snapshots = [
        ListingSnapshot(house_id="one", area=100.0, unit_price=50000.0),
        ListingSnapshot(house_id="two", area=100.5, unit_price=60000.0),
        ListingSnapshot(house_id="three", area=101.0, unit_price=70000.0),
        ListingSnapshot(house_id="peak-one", area=102.0, unit_price=80000.0),
        ListingSnapshot(house_id="peak-two", area=103.0, unit_price=80000.0),
        ListingSnapshot(house_id="peak-three", area=104.0, unit_price=80000.0),
    ]

    filtered, applied_tolerance = filter_snapshots_by_area_with_fallback(
        snapshots,
        100.0,
    )

    assert [item.house_id for item in filtered] == ["one", "two", "three"]
    assert applied_tolerance == 1.0


def test_area_reference_uses_single_listing_within_maximum():
    snapshots = [
        ListingSnapshot(
            house_id="near",
            community_name="target",
            area=109.0,
            unit_price=50000.0,
        ),
    ]

    filtered, quote_prices, reference = prepare_listing_data_with_reference(
        snapshots,
        "target",
        100.0,
    )

    assert [item.house_id for item in filtered] == ["near"]
    assert quote_prices == [50000.0]
    assert reference == {
        "reference_code": "WEAK_AREA_REFERENCE",
        "reference_area_tolerance": 9.0,
        "reference_area_min": 91.0,
        "reference_area_max": 109.0,
        "reference_listing_count": 1,
    }


def test_prepare_listing_data_marks_strict_single_listing_as_weak_reference():
    snapshots = [
        ListingSnapshot(
            house_id="strict",
            community_name="target",
            area=100.0,
            unit_price=50000.0,
        ),
    ]

    filtered, quote_prices, reference = prepare_listing_data_with_reference(
        snapshots,
        "target",
        100.0,
    )

    assert [item.house_id for item in filtered] == ["strict"]
    assert quote_prices == [50000.0]
    assert reference == {
        "reference_code": "WEAK_AREA_REFERENCE",
        "reference_area_tolerance": 1.0,
        "reference_area_min": 99.0,
        "reference_area_max": 101.0,
        "reference_listing_count": 1,
    }


def test_area_reference_keeps_multiple_price_peaks_within_maximum():
    snapshots = [
        ListingSnapshot(house_id="one", community_name="target", area=97.71, unit_price=70299.0),
        ListingSnapshot(house_id="two", community_name="target", area=95.79, unit_price=54026.0),
        ListingSnapshot(house_id="three", community_name="target", area=97.57, unit_price=70171.0),
        ListingSnapshot(house_id="four", community_name="target", area=95.79, unit_price=54027.0),
        ListingSnapshot(house_id="five", community_name="target", area=94.13, unit_price=59543.0),
        ListingSnapshot(house_id="outside", community_name="target", area=75.0, unit_price=40000.0),
    ]

    filtered, quote_prices, reference = prepare_listing_data_with_reference(
        snapshots,
        "target",
        100.0,
    )

    assert [item.house_id for item in filtered] == ["one", "two", "three", "four", "five"]
    assert quote_prices == [70299.0, 54026.0, 70171.0, 54027.0, 59543.0]
    assert reference == {
        "reference_code": "WEAK_AREA_REFERENCE",
        "reference_area_tolerance": 5.87,
        "reference_area_min": 94.13,
        "reference_area_max": 105.87,
        "reference_listing_count": 5,
    }


def test_filter_snapshots_by_area_does_not_widen_when_strict_has_hit():
    snapshots = [
        ListingSnapshot(house_id="strict", area=100.0),
        ListingSnapshot(house_id="fallback", area=109.0),
    ]

    filtered, applied_tolerance = filter_snapshots_by_area_with_fallback(
        snapshots,
        100.0,
    )

    assert [item.house_id for item in filtered] == ["strict"]
    assert applied_tolerance == 1.0


def test_prepare_listing_data_keeps_snapshots_and_prices_from_same_area_batch():
    """面积过滤后明细和在售单价必须来自同一批快照。"""
    snapshots = [
        ListingSnapshot(
            house_id="1", community_name="示例花园", area=99.5, unit_price=65000.0
        ),
        ListingSnapshot(
            house_id="2", community_name="示例花园", area=120.0, unit_price=48000.0
        ),
    ]

    filtered, quote_prices = prepare_listing_data(snapshots, "示例花园", 100.0)

    assert [item.house_id for item in filtered] == ["1"]
    assert quote_prices == [65000.0]


def test_listing_no_data_reason_distinguishes_area_miss():
    """命中小区但没有目标面积房源时，原因必须明确指出面积范围。"""
    snapshots = [
        ListingSnapshot(house_id="1", community_name="示例花园", area=70.0)
    ]

    summary = listing_filter_summary(snapshots, "示例花园", 100.0)
    reason = listing_no_data_reason(snapshots, "示例花园", 100.0)

    assert "命中小区 1 条" in summary
    assert "命中面积 0 条" in summary
    assert "命中小区但无请求面积±1㎡房源" in reason
    assert "99.00~101.00㎡" in reason
    assert listing_no_data_status(snapshots, "示例花园", 100.0) == "NO_MATCHING_AREA"


def test_listing_no_data_status_keeps_plain_no_data_for_community_miss():
    """没有命中目标小区时继续使用普通 NO_DATA。"""
    snapshots = [ListingSnapshot(house_id="1", community_name="其他小区", area=100.0)]

    assert listing_no_data_status(snapshots, "示例花园", 100.0) == "NO_DATA"
