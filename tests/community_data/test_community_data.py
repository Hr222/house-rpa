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


def test_bare_group_name_query_rejected_for_multi_phase(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    first = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园一期", estate_type="住宅")
    )
    second = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园二期", estate_type="住宅")
    )

    assert first.community_id != second.community_id
    assert first.community_group_id == second.community_group_id
    # 不允许粗粒度查询：未指定期数且命中多条记录时拒绝，返回空。
    assert service.resolve_communities("深圳", "南山区", "示例花园") == []


def test_unique_bare_alias_resolves_single_phase_record(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    first = service.add_seed(
        CommunitySeed(
            "深圳",
            "宝安区",
            "示例都一期",
            aliases=("示例都花园",),
            estate_type="住宅",
        )
    )
    service.add_seed(CommunitySeed("深圳", "宝安区", "示例都二期", estate_type="住宅"))

    records = service.resolve_communities("深圳", "宝安区", "示例都花园")

    assert [record.community_id for record in records] == [first.community_id]


def test_phaseless_record_name_beats_sibling_alias(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    base = service.add_seed(
        CommunitySeed("深圳", "罗湖区", "示例福花园", estate_type="住宅")
    )
    service.add_seed(
        CommunitySeed(
            "深圳",
            "罗湖区",
            "示例福花园二期",
            aliases=("示例福花园",),
            estate_type="住宅",
        )
    )

    records = service.resolve_communities("深圳", "罗湖区", "示例福花园")

    # 正式名相等的无期数记录优先，不展开兄弟期数。
    assert [record.community_id for record in records] == [base.community_id]


def test_phase_alias_resolves_existing_record_without_creating_duplicate(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    service.add_seed(
        CommunitySeed(
            "深圳",
            "南山区",
            "城市天地广场",
            aliases=("城市天地广场一期",),
         estate_type="住宅")
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


def test_digit_and_chinese_phase_names_resolve_to_same_record(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    seeded = service.add_seed(CommunitySeed("深圳", "南山区", "示例海岸3期", estate_type="住宅"))

    records = service.resolve_communities("深圳", "南山区", "示例海岸三期")

    assert [record.community_id for record in records] == [seeded.community_id]
    assert geocoder.calls == []


def test_paren_phase_query_returns_only_matching_phase(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    second = service.add_seed(
        CommunitySeed(
            "深圳",
            "盐田区",
            "倚山花园二期",
            aliases=("新世界倚山花园二期",),
         estate_type="住宅")
    )
    third = service.add_seed(
        CommunitySeed(
            "深圳",
            "盐田区",
            "倚山花园三期",
            aliases=("新世界倚山花园三期",),
         estate_type="住宅")
    )

    records = service.resolve_communities("深圳", "盐田区", "新世界倚山花园(三期)")

    assert [record.community_id for record in records] == [third.community_id]
    assert second.community_id not in {record.community_id for record in records}


def test_paren_sub_name_falls_back_to_main_record(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    seeded = service.add_seed(CommunitySeed("深圳", "坪山区", "大东城二期", estate_type="住宅"))

    records = service.resolve_communities("深圳", "坪山区", "大东城二期（嘉宏湾花园二期）")

    assert [record.community_id for record in records] == [seeded.community_id]
    assert geocoder.calls == []


def test_abbreviation_fallback_resolves_full_name_and_rejects_generic(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    seeded = service.add_seed(CommunitySeed("深圳", "龙岗区", "深业泰瑞府", estate_type="住宅"))

    records = service.resolve_communities("深圳", "龙岗区", "泰瑞府")
    generic = service.resolve_communities("深圳", "龙岗区", "泰府")

    assert [record.community_id for record in records] == [seeded.community_id]
    assert generic == []
    assert geocoder.calls == []


def test_add_alias_is_idempotent_and_keeps_existing_aliases(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    seeded = service.add_seed(
        CommunitySeed("深圳", "龙岗区", "深业泰瑞府", aliases=("泰瑞府",), estate_type="住宅")
    )

    assert service.database.add_alias(seeded.community_id, "泰瑞府") is False
    assert service.database.add_alias(seeded.community_id, "大运新城府") is True

    record = service.database.get_by_id(seeded.community_id)
    assert record.aliases == ("泰瑞府", "大运新城府")


def test_resolve_filters_out_non_residential(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    service.add_seed(CommunitySeed("深圳", "南山区", "示例花园", estate_type="住宅"))
    service.add_seed(CommunitySeed("深圳", "南山区", "示例大厦", estate_type="非住宅"))

    records = service.resolve_communities("深圳", "南山区", "示例大厦")

    assert records == []
    assert [
        record.name
        for record in service.resolve_communities("深圳", "南山区", "示例花园")
    ] == ["示例花园"]


def test_resolve_excludes_unverified_estate_type(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    service.add_seed(CommunitySeed("深圳", "南山区", "示例花园"))

    records = service.resolve_communities("深圳", "南山区", "示例花园")

    assert records == []


def test_nearby_filters_out_non_residential(tmp_path: Path) -> None:
    service, geocoder = make_service(tmp_path)
    service.add_seed(
        CommunitySeed(
            "深圳",
            "南山区",
            "中心花园",
            build_year=2010,
            estate_type="住宅",
        )
    )
    service.add_seed(
        CommunitySeed(
            "深圳",
            "南山区",
            "隔壁住宅",
            build_year=2010,
            estate_type="住宅",
        )
    )
    service.add_seed(
        CommunitySeed(
            "深圳",
            "南山区",
            "隔壁公寓",
            build_year=2015,
            estate_type="公寓",
        )
    )
    service.geocode_pending()

    records = service.find_nearby_communities("深圳", "南山区", "中心花园")

    assert [record.name for record in records] == ["隔壁住宅"]


def test_nearby_prefers_year_closeness_within_distance_band(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    center = service.add_seed(
        CommunitySeed("深圳", "南山区", "中心花园", build_year=2023, estate_type="住宅")
    )
    older_near = service.add_seed(
        CommunitySeed("深圳", "南山区", "近处旧苑", build_year=2019, estate_type="住宅")
    )
    newer_near = service.add_seed(
        CommunitySeed("深圳", "南山区", "近处新苑", build_year=2022, estate_type="住宅")
    )
    same_year_farther = service.add_seed(
        CommunitySeed("深圳", "南山区", "稍远新苑", build_year=2023, estate_type="住宅")
    )

    base_longitude, base_latitude = 113.95, 22.54
    meters_per_degree = 111_195.0
    placements = {
        center.community_id: 0.0,
        older_near.community_id: 1_000.0,
        newer_near.community_id: 1_800.0,
        same_year_farther.community_id: 2_500.0,
    }
    for community_id, meters in placements.items():
        service.database.update_geocode(
            community_id,
            GeocodeResult(
                longitude=base_longitude,
                latitude=base_latitude + meters / meters_per_degree,
                level=10,
                reliability=7,
            ),
        )

    records = service.find_nearby_communities("深圳", "南山区", "中心花园")

    # 同处 0-2km 带内，年份差小的近处新苑优先于更近的近处旧苑；
    # 2-5km 带的稍远新苑虽然同年，也不能越过更近的距离带。
    assert [record.name for record in records] == ["近处新苑", "近处旧苑", "稍远新苑"]


def test_nearby_counts_each_phase_as_one_result(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    center = service.add_seed(
        CommunitySeed("深圳", "南山区", "中心花园", build_year=2023, estate_type="住宅")
    )
    phase_one = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园一期", build_year=2022, estate_type="住宅")
    )
    phase_two = service.add_seed(
        CommunitySeed("深圳", "南山区", "示例花园二期", build_year=2023, estate_type="住宅")
    )
    other = service.add_seed(
        CommunitySeed("深圳", "南山区", "别家苑", build_year=2024, estate_type="住宅")
    )

    base_longitude, base_latitude = 113.95, 22.54
    meters_per_degree = 111_195.0
    placements = {
        center.community_id: 0.0,
        phase_one.community_id: 500.0,
        phase_two.community_id: 600.0,
        other.community_id: 700.0,
    }
    for community_id, meters in placements.items():
        service.database.update_geocode(
            community_id,
            GeocodeResult(
                longitude=base_longitude,
                latitude=base_latitude + meters / meters_per_degree,
                level=10,
                reliability=7,
            ),
        )

    records = service.find_nearby_communities(
        "深圳", "南山区", "中心花园", limit=2
    )

    # 每条期数记录算一条数据：同年的二期(600米)优先于差一年的一期(500米)，
    # limit=2 按条数截断，第三条的别家苑不返回。
    assert [record.name for record in records] == ["示例花园二期", "示例花园一期"]
