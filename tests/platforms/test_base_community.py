# -*- coding: utf-8 -*-
"""RPA 小区归属的关键测试。"""

from app.rpa.core.models import ListingSnapshot
from app.rpa.platforms.base import filter_snapshots_by_community


def test_filter_uses_structured_community_name_and_rejects_unrelated_listing():
    snapshots = [
        ListingSnapshot(house_id="1", community_name="华润静安府"),
        ListingSnapshot(house_id="2", community_name="静安府"),
        ListingSnapshot(
            house_id="3",
            community_name="其他小区",
            title="静安府精装三房",
        ),
    ]

    filtered = filter_snapshots_by_community(snapshots, "静安府")

    assert [item.house_id for item in filtered] == ["1", "2"]


def test_phase_mismatch_rejects_sibling_phase_listing():
    snapshots = [
        ListingSnapshot(house_id="1", community_name="尚都一期"),
        ListingSnapshot(house_id="2", community_name="尚都二期"),
        # 合并页抓取的小区名不带期数，双写下仍应命中。
        ListingSnapshot(house_id="3", community_name="尚都"),
        ListingSnapshot(house_id="4", community_name="蔚蓝海岸南山4期"),
    ]

    filtered = filter_snapshots_by_community(snapshots, "尚都二期")

    assert [item.house_id for item in filtered] == ["2", "3"]


def test_digit_and_chinese_phase_still_match():
    snapshots = [
        ListingSnapshot(house_id="1", community_name="蔚蓝海岸南山4期"),
        ListingSnapshot(house_id="2", community_name="蔚蓝海岸三期"),
    ]

    filtered = filter_snapshots_by_community(snapshots, "蔚蓝海岸四期")

    assert [item.house_id for item in filtered] == ["1"]
