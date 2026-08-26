# -*- coding: utf-8 -*-
"""小区基础数据模块测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.community_data import config
from app.community_data.database import CommunityDatabase
from app.community_data.geocoder import GeocodeResult
from app.community_data.models import CommunitySeed, GEOCODE_SUCCESS
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


def test_insert_uses_own_id_and_groups_phases(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    first = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园一期")
    )
    second = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园二期")
    )

    assert first.community_id != second.community_id
    assert first.community_group_id == second.community_group_id
    assert [record.name for record in service.resolve_communities("深圳", "南山区", "示例花园")] == [
        "示例花园一期",
        "示例花园二期",
    ]


def test_existing_record_does_not_call_geocoder(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    service.add_seed(CommunitySeed("深圳", "南山区", "已有小区"))
    records = service.resolve_communities("深圳", "南山区", "已有小区")

    assert len(records) == 1
    assert geocoder.calls == []


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


def test_missing_record_calls_geocoder_once_and_persists(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    records = service.resolve_communities("深圳", "南山区", "新小区")

    assert len(records) == 1
    assert len(geocoder.calls) == 1
    assert records[0].geocode_status == GEOCODE_SUCCESS
    assert records[0].longitude is not None

    service.resolve_communities("深圳", "南山区", "新小区")
    assert len(geocoder.calls) == 1


def test_nearby_limit_counts_groups_then_expands_phases(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    center = service.add_seed(
        CommunitySeed(
            "深圳",
            "南山区",
            "中心花园一期",
            aliases=("中心别名",),
            build_year=2015,
        )
    )
    service.database.update_geocode(center.community_id, GeocodeResult(113.9500, 22.5400, 10, 7))

    grouped = [
        ("一号花园一期", 113.9540, 22.5400, 2016),
        ("一号花园二期", 113.9540, 22.5400, 2016),
        ("二号花园", 113.9510, 22.5400, 2012),
        ("三号花园", 113.9520, 22.5400, 2020),
        ("四号花园", 113.9530, 22.5400, 2009),
    ]
    for name, longitude, latitude, build_year in grouped:
        record = service.add_seed(
            CommunitySeed("深圳", "南山区", name, build_year=build_year)
        )
        service.database.update_geocode(
            record.community_id,
            GeocodeResult(longitude, latitude, 10, 7),
        )

    nearby = service.find_nearby_communities("深圳", "南山区", "中心别名")

    assert [record.name for record in nearby] == [
        "二号花园",
        "三号花园",
        "一号花园一期",
        "一号花园二期",
    ]
    assert len({record.community_group_id for record in nearby}) == 3

    all_nearby = service.find_nearby_communities(
        "深圳",
        "南山区",
        "中心别名",
        filter_by_build_year=False,
    )
    assert [record.name for record in all_nearby] == [
        "二号花园",
        "三号花园",
        "四号花园",
    ]


def test_nearby_missing_center_uses_same_insert_and_geocode_flow(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    nearby = service.find_nearby_communities("深圳", "南山区", "新中心", 3)

    assert nearby == []
    assert geocoder.calls == ["深圳市南山区新中心"]


def test_nearby_excludes_communities_outside_configured_radius(tmp_path: Path, monkeypatch) -> None:
    service, _ = make_service(tmp_path)
    center = service.add_seed(CommunitySeed("深圳", "南山区", "中心花园", build_year=2015))
    nearby = service.add_seed(CommunitySeed("深圳", "南山区", "近处花园", build_year=2016))
    distant = service.add_seed(CommunitySeed("深圳", "南山区", "远处花园", build_year=2016))
    service.database.update_geocode(center.community_id, GeocodeResult(113.9500, 22.5400, 10, 7))
    service.database.update_geocode(nearby.community_id, GeocodeResult(113.9510, 22.5400, 10, 7))
    service.database.update_geocode(distant.community_id, GeocodeResult(113.9600, 22.5400, 10, 7))
    monkeypatch.setattr(config, "NEARBY_RADIUS_METERS", 200.0)

    records = service.find_nearby_communities("深圳", "南山区", "中心花园")

    assert [record.name for record in records] == ["近处花园"]


def test_nearby_excludes_out_of_radius_phases_in_selected_group(tmp_path: Path, monkeypatch) -> None:
    service, _ = make_service(tmp_path)
    center = service.add_seed(CommunitySeed("深圳", "南山区", "中心花园", build_year=2015))
    nearby_phase = service.add_seed(CommunitySeed("深圳", "南山区", "候选花园一期", build_year=2016))
    distant_phase = service.add_seed(CommunitySeed("深圳", "南山区", "候选花园二期", build_year=2016))
    service.database.update_geocode(center.community_id, GeocodeResult(113.9500, 22.5400, 10, 7))
    service.database.update_geocode(nearby_phase.community_id, GeocodeResult(113.9510, 22.5400, 10, 7))
    service.database.update_geocode(distant_phase.community_id, GeocodeResult(113.9600, 22.5400, 10, 7))
    monkeypatch.setattr(config, "NEARBY_RADIUS_METERS", 200.0)

    records = service.find_nearby_communities("深圳", "南山区", "中心花园")

    assert [record.name for record in records] == ["候选花园一期"]
    assert records[0].distance_meters is not None


def test_nearby_excludes_candidates_in_another_coordinate_system(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    center = service.add_seed(CommunitySeed("深圳", "南山区", "中心花园", build_year=2015))
    valid = service.add_seed(CommunitySeed("深圳", "南山区", "有效花园", build_year=2016))
    invalid = service.add_seed(CommunitySeed("深圳", "南山区", "错误坐标花园", build_year=2016))
    service.database.update_geocode(center.community_id, GeocodeResult(113.9500, 22.5400, 10, 7))
    service.database.update_geocode(valid.community_id, GeocodeResult(113.9510, 22.5400, 10, 7))
    service.database.update_geocode(
        invalid.community_id,
        GeocodeResult(113.9510, 22.5400, 10, 7, coordinate_system="WGS-84"),
    )

    records = service.find_nearby_communities("深圳", "南山区", "中心花园")

    assert [record.name for record in records] == ["有效花园"]


@pytest.mark.parametrize("value", ("nan", "inf", "-inf", "0", "-10"))
def test_non_positive_or_non_finite_radius_uses_default(monkeypatch, value: str) -> None:
    monkeypatch.setenv(config.NEARBY_RADIUS_ENV, value)

    radius = config._positive_float_from_env(
        config.NEARBY_RADIUS_ENV,
        config.DEFAULT_NEARBY_RADIUS_METERS,
    )

    assert radius == config.DEFAULT_NEARBY_RADIUS_METERS


def test_build_year_pending_list_and_update(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    record = service.add_seed(CommunitySeed("深圳", "罗湖区", "待回填小区"))

    assert [item.community_id for item in service.database.list_pending_build_year()] == [
        record.community_id
    ]
    service.database.update_build_year(record.community_id, 2004)
    assert service.database.list_pending_build_year() == []
    assert service.database.find("深圳", "罗湖区", "待回填小区")[0].build_year == 2004


@pytest.mark.parametrize("build_year", (1799, 2101, "invalid"))
def test_build_year_is_validated_on_initial_insert(tmp_path: Path, build_year) -> None:
    service, _ = make_service(tmp_path)

    with pytest.raises(ValueError, match="build_year 不合法"):
        service.add_seed(
            CommunitySeed(
                "深圳",
                "罗湖区",
                "年份异常小区",
                build_year=build_year,
            )
        )


@pytest.mark.parametrize(
    ("longitude", "latitude"),
    ((float("nan"), 22.5), (181, 22.5), (113.9, 91)),
)
def test_coordinates_are_validated_before_persisting(
    tmp_path: Path,
    longitude: float,
    latitude: float,
) -> None:
    service, _ = make_service(tmp_path)
    record = service.add_seed(CommunitySeed("深圳", "罗湖区", "坐标异常小区"))

    with pytest.raises(ValueError, match="经纬度|经度|纬度"):
        service.database.update_geocode(
            record.community_id,
            GeocodeResult(longitude, latitude, 10, 7),
        )
