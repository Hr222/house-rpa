# -*- coding: utf-8 -*-
"""小区数据对外服务入口。"""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import Protocol

from app.community_data import config
from app.community_data.database import CommunityDatabase
from app.community_data.geocoder import GeocodeResult, TencentGeocoder
from app.community_data.models import COORDINATE_SYSTEM, CommunityRecord, CommunitySeed


log = logging.getLogger(__name__)
EARTH_RADIUS_METERS = 6_371_008.8
BUILD_YEAR_MATCH_RANGE = 5


class Geocoder(Protocol):
    """地理编码器最小接口，便于测试替换。"""

    def geocode(self, address: str) -> GeocodeResult:
        ...


class CommunityDataService:
    """提供人工维护小区主数据的查询与显式维护能力。"""

    def __init__(
        self,
        database: CommunityDatabase | None = None,
        geocoder: Geocoder | None = None,
    ) -> None:
        self.database = database or CommunityDatabase()
        self.geocoder = geocoder or TencentGeocoder()

    def resolve_communities(
        self,
        city: str,
        administrative_district: str,
        community_name: str,
    ) -> list[CommunityRecord]:
        """只查询人工维护的小区记录，不在请求链路自动新增或地理编码。"""
        return self.database.find(city, administrative_district, community_name)

    def add_seed(self, seed: CommunitySeed) -> CommunityRecord:
        """导入一次性基础数据，不自动调用腾讯地图。"""
        return self.database.insert_or_get(seed)

    def geocode_pending(self, limit: int | None = None) -> int:
        """按显式数量补齐待处理坐标，返回成功数量。"""
        if limit is not None and limit <= 0:
            raise ValueError("limit 必须是正整数")
        success_count = 0
        for record in self.database.list_pending_geocode(limit):
            try:
                result = self.geocoder.geocode(record.address)
            except Exception as exc:
                log.warning("小区地理编码失败: community_id=%s, error=%s", record.community_id, exc)
                self.database.mark_geocode_failed(record.community_id, str(exc))
                continue
            self.database.update_geocode(record.community_id, result)
            success_count += 1
        return success_count

    def find_nearby_communities(
        self,
        city: str,
        administrative_district: str,
        community_name: str,
        limit: int = 3,
        filter_by_build_year: bool = True,
    ) -> list[CommunityRecord]:
        """查询附近小区。

        默认只返回建成年份与中心小区相差不超过五年的记录，始终优先按
        地理距离排序；距离相同时再参考建成年份差距。关闭年份筛选时，
        按距离返回小区组及其全部期数。
        """
        if limit <= 0:
            raise ValueError("limit 必须是正整数")

        origins = self.resolve_communities(
            city,
            administrative_district,
            community_name,
        )
        origin_group_ids = {record.community_group_id for record in origins}
        origin_points = [
            (record.latitude, record.longitude)
            for record in origins
            if (
                record.latitude is not None
                and record.longitude is not None
                and record.coordinate_system == COORDINATE_SYSTEM
            )
        ]
        if not origin_points:
            log.info("中心小区没有可用坐标: city=%s, name=%s", city, community_name)
            return []

        origin_build_years = tuple(
            record.build_year for record in origins if record.build_year is not None
        )
        if filter_by_build_year and not origin_build_years:
            log.info(
                "中心小区没有建成年份，无法按年份筛选附近小区: city=%s, name=%s",
                city,
                community_name,
            )
            return []

        candidates = self.database.list_city_with_coordinates(city)
        group_distances: dict[int, float] = {}
        candidate_distances: dict[int, float] = {}
        eligible_candidate_ids: set[int] = set()
        group_year_differences: dict[int, int] = {}
        for candidate in candidates:
            if candidate.community_group_id in origin_group_ids:
                continue
            distance = min(
                _haversine_meters(origin_lat, origin_lng, candidate.latitude, candidate.longitude)
                for origin_lat, origin_lng in origin_points
            )
            if distance > config.NEARBY_RADIUS_METERS:
                continue
            candidate_distances[candidate.community_id] = distance
            if filter_by_build_year:
                if candidate.build_year is None:
                    continue
                year_difference = min(
                    abs(candidate.build_year - origin_build_year)
                    for origin_build_year in origin_build_years
                )
                if year_difference > BUILD_YEAR_MATCH_RANGE:
                    continue
                eligible_candidate_ids.add(candidate.community_id)
                current_year_difference = group_year_differences.get(candidate.community_group_id)
                if current_year_difference is None or year_difference < current_year_difference:
                    group_year_differences[candidate.community_group_id] = year_difference
            current = group_distances.get(candidate.community_group_id)
            if current is None or distance < current:
                group_distances[candidate.community_group_id] = distance

        if filter_by_build_year:
            group_distances = {
                group_id: distance
                for group_id, distance in group_distances.items()
                if group_id in group_year_differences
            }

        selected_groups = [
            group_id
            for group_id, _distance in sorted(
                group_distances.items(),
                key=lambda item: (
                    item[1],
                    group_year_differences[item[0]],
                    item[0],
                )
                if filter_by_build_year
                else (item[1], item[0]),
            )[:limit]
        ]
        if not selected_groups:
            return []

        expanded = self.database.list_by_group_ids(selected_groups)
        eligible_ids = eligible_candidate_ids if filter_by_build_year else candidate_distances
        expanded = [record for record in expanded if record.community_id in eligible_ids]
        group_order = {group_id: index for index, group_id in enumerate(selected_groups)}
        expanded.sort(
            key=lambda record: (
                group_order[record.community_group_id],
                (
                    min(abs(record.build_year - origin_build_year) for origin_build_year in origin_build_years)
                    if filter_by_build_year and record.build_year is not None
                    else 0
                ),
                record.phase or "",
                record.community_id,
            )
        )
        return [
            replace(
                record,
                distance_meters=candidate_distances.get(record.community_id),
            )
            for record in expanded
        ]


def _haversine_meters(
    latitude_a: float | None,
    longitude_a: float | None,
    latitude_b: float | None,
    longitude_b: float | None,
) -> float:
    if None in {latitude_a, longitude_a, latitude_b, longitude_b}:
        raise ValueError("计算距离需要四个坐标值")
    lat_a = math.radians(float(latitude_a))
    lat_b = math.radians(float(latitude_b))
    delta_lat = lat_b - lat_a
    delta_lng = math.radians(float(longitude_b) - float(longitude_a))
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(value))


_default_service: CommunityDataService | None = None


def _get_default_service() -> CommunityDataService:
    global _default_service
    if _default_service is None:
        _default_service = CommunityDataService()
    return _default_service


def resolve_communities(
    city: str,
    administrative_district: str,
    community_name: str,
) -> list[CommunityRecord]:
    """RPA 对外查询插口。"""
    return _get_default_service().resolve_communities(
        city,
        administrative_district,
        community_name,
    )


def find_nearby_communities(
    city: str,
    administrative_district: str,
    community_name: str,
    limit: int = 3,
    filter_by_build_year: bool = True,
) -> list[CommunityRecord]:
    """附近小区查询接口，支持别名和默认建成年份 ±5 年筛选。"""
    return _get_default_service().find_nearby_communities(
        city,
        administrative_district,
        community_name,
        limit,
        filter_by_build_year,
    )
