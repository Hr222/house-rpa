# -*- coding: utf-8 -*-
"""平台基类小区匹配辅助函数测试。"""

from app.core.models import ListingSnapshot
from app.platforms.base import (
    community_name_match,
    filter_snapshots_by_community,
    has_matching_community_snapshots,
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
