# -*- coding: utf-8 -*-
"""小区身份解析的关键测试。"""

from __future__ import annotations

from pathlib import Path

from app.community_data.database import CommunityDatabase
from app.community_data.geocoder import GeocodeResult
from app.community_data.models import CommunitySeed
from app.community_data.service import CommunityDataService


class FakeGeocoder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def geocode(self, address: str) -> GeocodeResult:
        self.calls.append(address)
        return GeocodeResult(
            longitude=113.9500 + len(self.calls) / 10000,
            latitude=22.5400 + len(self.calls) / 10000,
            level=10,
            reliability=7,
        )


def make_service(tmp_path: Path) -> tuple[CommunityDataService, FakeGeocoder]:
    geocoder = FakeGeocoder()
    service = CommunityDataService(
        database=CommunityDatabase(tmp_path / "community.sqlite3"),
        geocoder=geocoder,
    )
    return service, geocoder


def test_resolve_group_keeps_distinct_phase_ids(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    first = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园一期")
    )
    second = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园二期")
    )

    assert first.community_id != second.community_id
    assert first.community_group_id == second.community_group_id
    records = service.resolve_communities("深圳", "南山区", "示例花园")
    assert [record.name for record in records] == [
        "示例花园一期",
        "示例花园二期",
    ]
    assert {record.community_id for record in records} == {
        first.community_id,
        second.community_id,
    }


def test_phase_alias_resolves_existing_record_without_creating_duplicate(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    service.add_seed(
        CommunitySeed(
            "深圳",
            "南山区",
            "城市天地广场",
            aliases=("城市天地广场一期",),
        )
    )

    records = service.resolve_communities("深圳", "南山区", "城市天地广场一期")

    assert [record.name for record in records] == ["城市天地广场"]
    assert geocoder.calls == []
    assert len(service.database.list_pending_geocode()) == 1


def test_missing_record_returns_empty_without_persisting_or_geocoding(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    records = service.resolve_communities("深圳", "南山区", "新小区")

    assert records == []
    assert geocoder.calls == []
    assert service.database.find("深圳", "南山区", "新小区") == []
