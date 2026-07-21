# -*- coding: utf-8 -*-
"""安居客 adapter 回归测试。"""

from app.core.models import ListingSnapshot
from app.platforms.base import filter_snapshots_by_community


def test_filter_listing_snapshots_keeps_prefix_match_and_drops_unrelated():
    """保留前缀缩写匹配的小区，同时剔除目标外房源。"""
    snapshots = [
        ListingSnapshot(house_id="1", community_name="华润静安府", unit_price=26667.0),
        ListingSnapshot(house_id="2", community_name="前海丹华", unit_price=52000.0),
        ListingSnapshot(house_id="3", community_name="静安府", unit_price=30108.0),
    ]

    filtered = filter_snapshots_by_community(snapshots, "静安府")

    assert [item.community_name for item in filtered] == ["华润静安府", "静安府"]


def test_filter_listing_snapshots_returns_empty_when_no_community_matches():
    """没有任何匹配小区时返回空列表。"""
    snapshots = [
        ListingSnapshot(house_id="1", community_name="前海丹华", unit_price=52000.0),
        ListingSnapshot(house_id="2", community_name="半山臻境", unit_price=61000.0),
    ]

    assert filter_snapshots_by_community(snapshots, "静安府") == []
