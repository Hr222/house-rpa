# -*- coding: utf-8 -*-
"""小区 SQLite 数据库。"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ContextManager, Iterable, Optional

from app.community_data.geocoder import GeocodeResult
from app.community_data.models import (
    CommunityRecord,
    CommunitySeed,
    COORDINATE_SYSTEM,
    GEOCODE_FAILED,
    GEOCODE_PENDING,
    GEOCODE_SUCCESS,
)
from app.community_data.normalization import (
    group_name,
    normalize_name,
    phase_key,
    split_phase,
)
from app.persistence.sqlite import sqlite_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "persist" / "community_data.sqlite3"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "community_data_schema.sql"


class CommunityDatabase:
    """负责建表、写入、查询和坐标状态更新。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> ContextManager[sqlite3.Connection]:
        """返回统一管理事务和关闭生命周期的 SQLite 连接。"""
        return sqlite_connection(self.path)

    def _initialize(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as connection:
            connection.executescript(schema)

    def insert_or_get(self, seed: CommunitySeed) -> CommunityRecord:
        """按自有业务字段幂等写入一条记录并返回自有主键。"""
        city = seed.city.strip()
        administrative_district = seed.administrative_district.strip()
        name = seed.name.strip()
        if not city or not administrative_district or not name:
            raise ValueError("city、administrative_district、name 不能为空")

        base_name, phase = split_phase(name)
        normalized_full_name = normalize_name(name)
        normalized_group_name = group_name(name)
        normalized_phase = phase_key(phase)
        build_year = _validate_build_year(seed.build_year)
        aliases = tuple(
            alias.strip()
            for alias in seed.aliases
            if str(alias).strip()
        )
        now = _now()
        address = f"{_city_for_address(city)}{administrative_district}{name}"

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO community_groups (
                    city, administrative_district, normalized_name,
                    display_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    city,
                    administrative_district,
                    normalized_group_name,
                    base_name,
                    now,
                    now,
                ),
            )
            group_row = connection.execute(
                """
                SELECT community_group_id
                FROM community_groups
                WHERE city = ? AND administrative_district = ?
                  AND normalized_name = ?
                """,
                (city, administrative_district, normalized_group_name),
            ).fetchone()
            if group_row is None:
                raise RuntimeError("创建小区组后无法读取 community_group_id")

            connection.execute(
                """
                INSERT OR IGNORE INTO communities (
                    community_group_id, city, administrative_district, district,
                    name, normalized_name, phase, phase_key, aliases_json,
                    build_year, address, geocode_status, remark,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_row["community_group_id"],
                    city,
                    administrative_district,
                    seed.district,
                    name,
                    normalized_full_name,
                    phase,
                    normalized_phase,
                    json.dumps(aliases, ensure_ascii=False),
                    build_year,
                    address,
                    GEOCODE_PENDING,
                    seed.remark,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM communities
                WHERE city = ? AND administrative_district = ?
                  AND normalized_name = ? AND phase_key = ?
                """,
                (city, administrative_district, normalized_full_name, normalized_phase),
            ).fetchone()
            if row is None:
                raise RuntimeError("写入小区后无法读取 community_id")
            return _row_to_record(row)

    def find(self, city: str, administrative_district: str, community_name: str) -> list[CommunityRecord]:
        """按城市、行政区和小区名查询，支持未带期数的组名查询。"""
        city = city.strip()
        administrative_district = administrative_district.strip()
        query_name = normalize_name(community_name)
        query_group_name, query_phase = split_phase(community_name)
        normalized_group = normalize_name(query_group_name)
        normalized_query_phase = phase_key(query_phase)

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM communities
                WHERE city = ? AND administrative_district = ?
                ORDER BY community_group_id, phase_key, community_id
                """,
                (city, administrative_district),
            ).fetchall()

        records = [_row_to_record(row) for row in rows]
        if query_phase:
            return [
                record
                for record in records
                if _matches_name_and_phase(
                    record,
                    normalized_group,
                    normalized_query_phase,
                )
            ]

        matching_group_ids = {
            record.community_group_id
            for record in records
            if normalize_name(split_phase(record.name)[0]) == normalized_group
            or any(normalize_name(alias) == query_name for alias in record.aliases)
            or normalize_name(record.name) == query_name
        }
        return [
            record
            for record in records
            if record.community_group_id in matching_group_ids
        ]

    def get_by_id(self, community_id: int) -> Optional[CommunityRecord]:
        """按正式 community_id 读取一条记录，供其它模块做归属校验。"""
        try:
            normalized_id = int(community_id)
        except (TypeError, ValueError):
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM communities WHERE community_id = ?",
                (normalized_id,),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def list_city_with_coordinates(self, city: str) -> list[CommunityRecord]:
        """列出同城已有坐标的记录，用于附近计算。"""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM communities
                WHERE city = ? AND longitude IS NOT NULL AND latitude IS NOT NULL
                  AND coordinate_system = ?
                """,
                (city.strip(), COORDINATE_SYSTEM),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_by_group_ids(self, group_ids: Iterable[int]) -> list[CommunityRecord]:
        """展开小区组下的全部期数记录。"""
        ids = tuple(int(value) for value in group_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM communities
                WHERE community_group_id IN ({placeholders})
                ORDER BY community_group_id, phase_key, community_id
                """,
                ids,
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_pending_geocode(self, limit: int | None = None) -> list[CommunityRecord]:
        """列出尚未成功获取坐标的记录。"""
        sql = """
            SELECT * FROM communities
            WHERE longitude IS NULL OR latitude IS NULL
            ORDER BY community_id
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_pending_build_year(self, limit: int | None = None) -> list[CommunityRecord]:
        """列出尚未回填建成年份的小区记录。"""
        sql = """
            SELECT * FROM communities
            WHERE build_year IS NULL
            ORDER BY community_id
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def update_geocode(self, community_id: int, result: GeocodeResult) -> None:
        """保存成功的腾讯坐标。"""
        longitude, latitude = _validate_coordinates(
            result.longitude,
            result.latitude,
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE communities
                SET longitude = ?, latitude = ?, coordinate_system = ?,
                    geocode_status = ?, geocode_level = ?,
                    geocode_reliability = ?, geocode_message = NULL,
                    updated_at = ?
                WHERE community_id = ?
                """,
                (
                    longitude,
                    latitude,
                    result.coordinate_system,
                    GEOCODE_SUCCESS,
                    result.level,
                    result.reliability,
                    _now(),
                    community_id,
                ),
            )

    def mark_geocode_failed(self, community_id: int, message: str) -> None:
        """保存失败原因，不删除原始小区记录。"""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE communities
                SET geocode_status = ?, geocode_message = ?, updated_at = ?
                WHERE community_id = ?
                """,
                (GEOCODE_FAILED, str(message)[:1000], _now(), community_id),
            )

    def update_build_year(self, community_id: int, build_year: int) -> None:
        """保存已按外部证据确认的建成年份。"""
        year = _validate_build_year(build_year)
        if year is None:
            raise ValueError("build_year 不能为空")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE communities
                SET build_year = ?, updated_at = ?
                WHERE community_id = ?
                """,
                (year, _now(), community_id),
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _city_for_address(city: str) -> str:
    """为地理编码地址补充常见的“市”后缀，业务城市字段保持原样。"""
    if city.endswith(("市", "自治州", "地区", "盟")):
        return city
    return f"{city}市"


def _validate_coordinates(longitude: object, latitude: object) -> tuple[float, float]:
    """校验经纬度为有限数字且处于合法范围。"""
    try:
        normalized_longitude = float(longitude)
        normalized_latitude = float(latitude)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("经纬度必须是数字") from exc
    if not math.isfinite(normalized_longitude) or not math.isfinite(normalized_latitude):
        raise ValueError("经纬度必须是有限数字")
    if not -180 <= normalized_longitude <= 180:
        raise ValueError(f"经度不合法: {normalized_longitude}")
    if not -90 <= normalized_latitude <= 90:
        raise ValueError(f"纬度不合法: {normalized_latitude}")
    return normalized_longitude, normalized_latitude


def _validate_build_year(value: object | None) -> int | None:
    """校验建成年份；空值保留为空，便于后续人工回填。"""
    if value is None:
        return None
    try:
        year = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"build_year 不合法: {value!r}") from exc
    if year < 1800 or year > 2100:
        raise ValueError(f"build_year 不合法: {year}")
    return year


def _row_to_record(row: sqlite3.Row) -> CommunityRecord:
    try:
        aliases = tuple(json.loads(row["aliases_json"]))
    except (TypeError, json.JSONDecodeError):
        aliases = ()
    return CommunityRecord(
        community_id=int(row["community_id"]),
        community_group_id=int(row["community_group_id"]),
        city=row["city"],
        administrative_district=row["administrative_district"],
        district=row["district"],
        name=row["name"],
        phase=row["phase"],
        build_year=row["build_year"],
        aliases=aliases,
        address=row["address"],
        longitude=row["longitude"],
        latitude=row["latitude"],
        coordinate_system=row["coordinate_system"],
        geocode_status=row["geocode_status"],
        geocode_level=row["geocode_level"],
        geocode_reliability=row["geocode_reliability"],
        remark=row["remark"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _matches_name_and_phase(
    record: CommunityRecord,
    normalized_group: str,
    normalized_phase: str,
) -> bool:
    """匹配正式名或 Excel rename 中带期数的别名。"""
    for value in (record.name, *record.aliases):
        base_name, phase = split_phase(value)
        if (
            phase
            and phase_key(phase) == normalized_phase
            and normalize_name(base_name) == normalized_group
        ):
            return True
    return False
