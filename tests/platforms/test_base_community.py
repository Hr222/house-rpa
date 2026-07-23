# -*- coding: utf-8 -*-
"""平台基类小区匹配辅助函数测试。"""

from app.core.models import ListingSnapshot
from app.platforms.base import (
    community_name_match,
    filter_snapshots_by_area,
    filter_snapshots_by_area_with_fallback,
    filter_snapshots_by_community,
    has_matching_community_snapshots,
    listing_area_bounds,
    listing_filter_summary,
    listing_no_data_reason,
    listing_no_data_status,
    prepare_listing_data,
)


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


def test_filter_snapshots_by_area_falls_back_to_ten_when_strict_has_no_hit():
    snapshots = [
        ListingSnapshot(house_id="fallback", area=109.0),
        ListingSnapshot(house_id="outside", area=111.0),
    ]

    filtered, applied_tolerance = filter_snapshots_by_area_with_fallback(
        snapshots,
        100.0,
    )

    assert [item.house_id for item in filtered] == ["fallback"]
    assert applied_tolerance == 10.0


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
