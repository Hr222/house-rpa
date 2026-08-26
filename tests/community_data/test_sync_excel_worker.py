# -*- coding: utf-8 -*-
"""Excel 坐标回写工作进程测试。"""

from app.community_data.geocoder import GeocodeResult
from app.community_data.models import CommunityRecord, GEOCODE_PENDING, GEOCODE_SUCCESS
from scripts.community_data.sync_excel_worker import RequestRateLimiter, _sync_one


class FakeDatabase:
    def __init__(self) -> None:
        self.updated: list[tuple[int, GeocodeResult]] = []

    def update_geocode(self, community_id: int, result: GeocodeResult) -> None:
        self.updated.append((community_id, result))


class FakeGeocoder:
    def geocode(self, address: str) -> GeocodeResult:
        assert address == "深圳市罗湖区幸福华府"
        return GeocodeResult(114.108955, 22.548927, 10, 7)


class FakeService:
    def __init__(self) -> None:
        self.database = FakeDatabase()
        self.geocoder = FakeGeocoder()
        self.record = CommunityRecord(
            community_id=231,
            community_group_id=231,
            city="深圳",
            administrative_district="罗湖区",
            district="万象城",
            name="幸福华府",
            phase=None,
            build_year=None,
            aliases=(),
            address="深圳市罗湖区幸福华府",
            longitude=None,
            latitude=None,
            coordinate_system=None,
            geocode_status=GEOCODE_PENDING,
            geocode_level=None,
            geocode_reliability=None,
            remark=None,
            created_at="2026-08-25T00:00:00+00:00",
            updated_at="2026-08-25T00:00:00+00:00",
        )

    def add_seed(self, seed) -> CommunityRecord:
        assert seed.name == "幸福华府"
        return self.record


def test_geocode_result_uses_new_record_id_without_name_lookup() -> None:
    service = FakeService()

    result = _sync_one(
        {
            "city": "深圳",
            "area": "罗湖区",
            "district": "万象城",
            "name": "幸福华府",
        },
        service,
        RequestRateLimiter(1000),
        max_attempts=1,
        retry_delay_seconds=0,
    )

    assert result == {
        "status": "SUCCESS",
        "community_id": 231,
        "community_group_id": 231,
        "longitude": 114.108955,
        "latitude": 22.548927,
        "coordinate_system": "GCJ-02",
        "geocode_status": GEOCODE_SUCCESS,
    }
    assert [item[0] for item in service.database.updated] == [231]
