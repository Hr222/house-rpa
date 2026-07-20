# -*- coding: utf-8 -*-
"""平台基类小区匹配辅助函数测试。"""

from app.core.models import ListingSnapshot
from app.platforms.base import (
    check_page_community_match_rate,
    filter_snapshots_by_community,
    has_matching_community_snapshots,
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


def test_filter_and_match_rate_share_same_match_rule():
    """过滤结果和匹配率计算共用同一套小区匹配规则。"""
    snapshots = [
        ListingSnapshot(house_id="1", community_name="华润静安府"),
        ListingSnapshot(house_id="2", community_name="静安府"),
        ListingSnapshot(house_id="3", community_name="前海丹华"),
    ]

    filtered = filter_snapshots_by_community(snapshots, "静安府")

    assert [item.community_name for item in filtered] == ["华润静安府", "静安府"]
    assert check_page_community_match_rate(snapshots, "静安府") == 2 / 3
