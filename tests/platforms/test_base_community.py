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
