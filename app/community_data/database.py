# -*- coding: utf-8 -*-
"""小区 SQLite 数据库。"""

from __future__ import annotations

import json
import logging
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
    canonical_name_parts,
    group_name,
    normalize_name,
    paren_name_variants,
    phase_key,
    split_phase,
)
from app.persistence.sqlite import sqlite_connection


log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "persist" / "community_data.sqlite3"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "community_data_schema.sql"

# 简称兜底要求较短一方至少拥有的字符数，防止“花园”这类通名误配。
MIN_ABBREVIATED_NAME_CHARS = 3


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
                    estate_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    seed.estate_type,
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
        """按城市、行政区和小区名查询。

        匹配分三段，命中即返回：
        1. 精确匹配：正式名或别名的规范化字符串相等。
        2. 归一兜底：期数归一（“3期”等同“三期”）、尾部括号期数和括号主副名。
        3. 简称兜底：正式名包含简称，如“泰瑞府”对“深业泰瑞府”。

        期数约束：带期数的查询只返回该期记录；不带期数的查询不允许展开
        整组期数——命中多条记录时视为粗粒度查询，拒绝并返回空列表，
        调用方需指定期数后重查。
        """
        city = city.strip()
        administrative_district = administrative_district.strip()
        query_phase = split_phase(community_name)[1]

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
        matchers = (
            ("exact", self._match_by_exact_name),
            ("normalized_phase", self._match_by_normalized_phase),
            ("abbreviated_name", self._match_by_abbreviated_name),
        )
        for tier, matcher in matchers:
            matched = matcher(records, community_name)
            if not matched:
                continue
            if tier != "exact":
                log.info(
                    "小区查询命中%s兜底: city=%s district=%s name=%s matched=%s",
                    tier,
                    city,
                    administrative_district,
                    community_name,
                    [record.community_id for record in matched],
                )
            if query_phase is None and len(matched) > 1:
                log.warning(
                    "小区查询未指定期数且命中多条记录，已拒绝（需指定期数）: "
                    "city=%s district=%s name=%s matched=%s",
                    city,
                    administrative_district,
                    community_name,
                    [record.name for record in matched],
                )
                return []
            return matched
        return []

    def _match_by_exact_name(
        self,
        records: list[CommunityRecord],
        community_name: str,
    ) -> list[CommunityRecord]:
        """精确匹配：规范化字符串相等，不展开组内其他期数。

        带期数查询按“主名+期数”匹配正式名或带期数别名；不带期数查询只认
        正式名或别名的完整相等，正式名相等优先于别名相等。
        """
        query_name = normalize_name(community_name)
        query_group_name, query_phase = split_phase(community_name)

        if query_phase:
            normalized_group = normalize_name(query_group_name)
            return [
                record
                for record in records
                if _matches_name_and_phase(
                    record,
                    normalized_group,
                    phase_key(query_phase),
                )
            ]

        by_name = [
            record
            for record in records
            if normalize_name(record.name) == query_name
        ]
        if by_name:
            return by_name
        return [
            record
            for record in records
            if any(normalize_name(alias) == query_name for alias in record.aliases)
        ]

    def _match_by_normalized_phase(
        self,
        records: list[CommunityRecord],
        community_name: str,
    ) -> list[CommunityRecord]:
        """期数归一兜底：“3期”与“三期”视为同一期，括号期数与主副名参与匹配。"""
        query_parts = [
            canonical_name_parts(value)
            for value in (community_name, *paren_name_variants(community_name))
        ]
        matched_ids: set[int] = set()
        for record in records:
            if any(
                record_base == query_base
                and (query_phase is None or record_phase == query_phase)
                for record_base, record_phase in (
                    canonical_name_parts(value)
                    for value in self._record_name_variants(record)
                )
                for query_base, query_phase in query_parts
            ):
                matched_ids.add(record.community_id)
        return self._records_by_ids(records, matched_ids)

    def _match_by_abbreviated_name(
        self,
        records: list[CommunityRecord],
        community_name: str,
    ) -> list[CommunityRecord]:
        """简称兜底：正式名或别名以后缀包含简称，如“泰瑞府”对“深业泰瑞府”。"""
        query_parts = [
            canonical_name_parts(value)
            for value in (community_name, *paren_name_variants(community_name))
        ]
        matched_ids: set[int] = set()
        for record in records:
            for record_base, record_phase in (
                canonical_name_parts(value)
                for value in self._record_name_variants(record)
            ):
                for query_base, query_phase in query_parts:
                    if not record_base or not query_base:
                        continue
                    if (
                        min(len(record_base), len(query_base))
                        < MIN_ABBREVIATED_NAME_CHARS
                    ):
                        continue
                    if not (
                        record_base.endswith(query_base)
                        or query_base.endswith(record_base)
                    ):
                        continue
                    if query_phase is not None and record_phase != query_phase:
                        continue
                    matched_ids.add(record.community_id)
        return self._records_by_ids(records, matched_ids)

    def _record_name_variants(self, record: CommunityRecord) -> tuple[str, ...]:
        """参与匹配的记录侧名称：正式名、别名和正式名的括号主副名。"""
        return (
            record.name,
            *record.aliases,
            *paren_name_variants(record.name),
        )

    def _records_by_ids(
        self,
        records: list[CommunityRecord],
        community_ids: set[int],
    ) -> list[CommunityRecord]:
        """返回命中的具体记录，保持查询顺序。"""
        return [record for record in records if record.community_id in community_ids]

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

    def add_alias(self, community_id: int, alias: str) -> bool:
        """为记录追加人工确认的别名；别名已存在时不修改，返回是否新增。"""
        text = str(alias or "").strip()
        if not text:
            raise ValueError("alias 不能为空")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT aliases_json FROM communities WHERE community_id = ?",
                (int(community_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"community_id 不存在: {community_id}")
            try:
                aliases = list(json.loads(row["aliases_json"]))
            except (TypeError, json.JSONDecodeError):
                aliases = []
            if any(normalize_name(existing) == normalize_name(text) for existing in aliases):
                return False
            aliases.append(text)
            connection.execute(
                """
                UPDATE communities
                SET aliases_json = ?, updated_at = ?
                WHERE community_id = ?
                """,
                (json.dumps(aliases, ensure_ascii=False), _now(), int(community_id)),
            )
            return True


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
    keys = row.keys()
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
        estate_type=row["estate_type"] if "estate_type" in keys else None,
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
