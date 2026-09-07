# -*- coding: utf-8 -*-
"""房源记录模块 SQLite 数据访问。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ContextManager, Iterable

from app.community_data.database import CommunityDatabase
from app.persistence.sqlite import sqlite_connection
from app.property_records.models import (
    CommunityPlatformPage,
    DealRecord,
    ListingRecord,
    ListingRecordLog,
    UniqueDealRecord,
)
from app.property_records.normalization import (
    normalize_area_sqm,
    normalize_date,
    normalize_listing_url,
    normalize_page_url,
    normalize_total_price_yuan,
    normalize_unit_price_yuan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "persist" / "property_records.sqlite3"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "property_records_schema.sql"
DEAL_PLATFORMS = {"lj", "fang"}
LISTING_PLATFORMS = {"ke", "ajk", "fang", "lj", "lyj"}


class PropertyRecordsDatabase:
    """成交和挂牌记录的底层读写入口。"""

    def __init__(
        self,
        path: Path | str | None = None,
        community_database: CommunityDatabase | None = None,
    ) -> None:
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.community_database = community_database or CommunityDatabase()
        self._initialize()

    def _connect(self) -> ContextManager[sqlite3.Connection]:
        """返回统一管理事务和关闭生命周期的 SQLite 连接。"""
        return sqlite_connection(self.path)

    def _initialize(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as connection:
            connection.executescript(schema)
            self._migrate_legacy_community_page_tables(connection)

    @staticmethod
    def _migrate_legacy_community_page_tables(connection: sqlite3.Connection) -> None:
        """将旧的挂牌、成交入口表补入合并后的统一入口表。"""
        if _table_exists(connection, "community_listing_pages"):
            connection.execute(
                """
                INSERT INTO community_platform_pages (
                    community_id, source_platform, source_community_name,
                    listing_page_url
                )
                SELECT community_id, source_platform, NULL, listing_page_url
                FROM community_listing_pages
                WHERE listing_page_url IS NOT NULL
                ON CONFLICT (community_id, source_platform) DO UPDATE SET
                    listing_page_url = COALESCE(
                        community_platform_pages.listing_page_url,
                        excluded.listing_page_url
                    )
                """
            )
        if _table_exists(connection, "community_deal_pages"):
            connection.execute(
                """
                INSERT INTO community_platform_pages (
                    community_id, source_platform, source_community_name,
                    deal_page_url
                )
                SELECT
                    community_id, source_platform, source_community_name,
                    deal_page_url
                FROM community_deal_pages
                WHERE deal_page_url IS NOT NULL
                ON CONFLICT (community_id, source_platform) DO UPDATE SET
                    source_community_name = COALESCE(
                        community_platform_pages.source_community_name,
                        excluded.source_community_name
                    ),
                    deal_page_url = COALESCE(
                        community_platform_pages.deal_page_url,
                        excluded.deal_page_url
                    )
                """
            )

    def upsert_deal(
        self,
        *,
        community_id: int,
        city: str,
        administrative_district: str,
        source_platform: str,
        source_community_name: str,
        deal_date: object,
        area_sqm: object,
        total_price: object,
        unit_price_yuan: object,
    ) -> DealRecord:
        """写入一条成交事实；同来源五项完全一致时幂等更新。"""
        city, administrative_district = self._validate_community(
            community_id, city, administrative_district
        )
        if source_platform not in DEAL_PLATFORMS:
            raise ValueError(f"成交来源平台不支持: {source_platform}")
        source_name = _required_text(source_community_name, "source_community_name")
        normalized = (
            normalize_date(deal_date),
            normalize_area_sqm(area_sqm),
            normalize_total_price_yuan(total_price),
            normalize_unit_price_yuan(unit_price_yuan),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO deal_records (
                    community_id, city, administrative_district,
                    source_platform, source_community_name, deal_date,
                    area_sqm, total_price_yuan, unit_price_yuan
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    source_platform, community_id, deal_date, area_sqm,
                    total_price_yuan, unit_price_yuan
                ) DO UPDATE SET
                    city = excluded.city,
                    administrative_district = excluded.administrative_district,
                    source_community_name = excluded.source_community_name
                """,
                (
                    community_id,
                    city,
                    administrative_district,
                    source_platform,
                    source_name,
                    normalized[0],
                    normalized[1],
                    normalized[2],
                    normalized[3],
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM deal_records
                WHERE source_platform = ? AND community_id = ?
                  AND deal_date = ? AND area_sqm = ?
                  AND total_price_yuan = ? AND unit_price_yuan = ?
                """,
                (source_platform, community_id, *normalized),
            ).fetchone()
        if row is None:
            raise RuntimeError("写入成交记录后无法读取记录")
        return _deal_from_row(row)

    def upsert_community_platform_page(
        self,
        *,
        community_id: int,
        source_platform: str,
        source_community_name: str | None,
        listing_page_url: str | None = None,
        deal_page_url: str | None = None,
    ) -> CommunityPlatformPage:
        """保存小区在一个平台的已确认挂牌和成交入口。"""
        try:
            normalized_community_id = int(community_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"community_id 不存在: {community_id}") from exc
        if self.community_database.get_by_id(normalized_community_id) is None:
            raise ValueError(f"community_id 不存在: {community_id}")
        if source_platform not in LISTING_PLATFORMS:
            raise ValueError(f"平台页面来源不支持: {source_platform}")
        values = (
            normalized_community_id,
            source_platform,
            _optional_text(source_community_name),
            normalize_page_url(listing_page_url) if listing_page_url is not None else None,
            normalize_page_url(deal_page_url, keep_trailing_slash=True) if deal_page_url is not None else None,
        )
        if values[3] is None and values[4] is None:
            raise ValueError("listing_page_url 和 deal_page_url 不能同时为空")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO community_platform_pages (
                    community_id, source_platform, source_community_name,
                    listing_page_url, deal_page_url
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (community_id, source_platform) DO UPDATE SET
                    source_community_name = COALESCE(
                        excluded.source_community_name,
                        community_platform_pages.source_community_name
                    ),
                    listing_page_url = COALESCE(
                        excluded.listing_page_url,
                        community_platform_pages.listing_page_url
                    ),
                    deal_page_url = COALESCE(
                        excluded.deal_page_url,
                        community_platform_pages.deal_page_url
                    )
                """,
                values,
            )
            row = connection.execute(
                """
                SELECT * FROM community_platform_pages
                WHERE community_id = ? AND source_platform = ?
                """,
                (normalized_community_id, source_platform),
            ).fetchone()
        if row is None:
            raise RuntimeError("写入小区平台页面后无法读取记录")
        return _platform_page_from_row(row)

    def upsert_listing(
        self,
        *,
        community_id: int,
        city: str,
        administrative_district: str,
        source_platform: str,
        listing_url: str,
        source_community_name: str,
        title: str | None = None,
        layout: str | None = None,
        area_sqm: object | None = None,
        total_price: object | None = None,
        unit_price_yuan: object | None = None,
        seen_at: str | None = None,
    ) -> ListingRecord:
        """按来源平台和详情地址幂等更新当前挂牌状态。"""
        city, administrative_district = self._validate_community(
            community_id, city, administrative_district
        )
        if source_platform not in LISTING_PLATFORMS:
            raise ValueError(f"挂牌来源平台不支持: {source_platform}")
        source_name = _required_text(source_community_name, "source_community_name")
        url = normalize_listing_url(listing_url)
        observed_at = normalize_observed_at(seen_at)
        written_at = _now()
        if area_sqm is not None:
            area_sqm = normalize_area_sqm(area_sqm)
        if total_price is not None:
            total_price = normalize_total_price_yuan(total_price)
        if unit_price_yuan is not None:
            unit_price_yuan = normalize_unit_price_yuan(unit_price_yuan)

        with self._connect() as connection:
            # URL 归属检查和后续写入必须在同一个写事务中，避免并发写入时
            # 两个调用同时看到“URL 不存在”而产生归属竞争。
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM listing_records
                WHERE source_platform = ? AND listing_url = ?
                """,
                (source_platform, url),
            ).fetchone()
            if existing is not None and int(existing["community_id"]) != community_id:
                raise ValueError(
                    "同一来源地址已绑定其它 community_id，拒绝自动改绑: "
                    f"{source_platform} {url}"
                )
            if existing is None or not _timestamp_is_older(
                observed_at,
                existing["last_seen_at"],
            ):
                connection.execute(
                    """
                    INSERT INTO listing_records (
                        community_id, city, administrative_district,
                        source_platform, listing_url, source_community_name,
                        title, layout, area_sqm, total_price_yuan,
                        unit_price_yuan, is_deleted, last_seen_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    ON CONFLICT (source_platform, listing_url) DO UPDATE SET
                        source_community_name = excluded.source_community_name,
                        title = COALESCE(excluded.title, listing_records.title),
                        layout = COALESCE(excluded.layout, listing_records.layout),
                        area_sqm = COALESCE(excluded.area_sqm, listing_records.area_sqm),
                        total_price_yuan = COALESCE(
                            excluded.total_price_yuan,
                            listing_records.total_price_yuan
                        ),
                        unit_price_yuan = COALESCE(
                            excluded.unit_price_yuan,
                            listing_records.unit_price_yuan
                        ),
                        is_deleted = 0,
                        last_seen_at = excluded.last_seen_at,
                        updated_at = excluded.updated_at
                    WHERE listing_records.community_id = excluded.community_id
                      AND listing_records.last_seen_at <= excluded.last_seen_at
                    """,
                    (
                        community_id,
                        city,
                        administrative_district,
                        source_platform,
                        url,
                        source_name,
                        _optional_text(title),
                        _optional_text(layout),
                        area_sqm,
                        total_price,
                        unit_price_yuan,
                        observed_at,
                        written_at,
                        written_at,
                    ),
                )
            row = connection.execute(
                """
                SELECT * FROM listing_records
                WHERE source_platform = ? AND listing_url = ?
                """,
                (source_platform, url),
            ).fetchone()
            if row is not None and int(row["community_id"]) != community_id:
                raise ValueError(
                    "同一来源地址已绑定其它 community_id，拒绝自动改绑: "
                    f"{source_platform} {url}"
                )
            if row is not None and not _timestamp_is_older(
                observed_at,
                row["last_seen_at"],
            ):
                _insert_listing_log(connection, row)
        if row is None:
            raise RuntimeError("写入挂牌记录后无法读取记录")
        return _listing_from_row(row)

    def mark_listing_deleted(
        self,
        source_platform: str,
        listing_url: str,
        updated_at: str | None = None,
    ) -> ListingRecord | None:
        """将挂牌标记为逻辑删除，不物理删除。"""
        if source_platform not in LISTING_PLATFORMS:
            raise ValueError(f"挂牌来源平台不支持: {source_platform}")
        url = normalize_listing_url(listing_url)
        now = updated_at or _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE listing_records
                SET is_deleted = 1, updated_at = ?
                WHERE source_platform = ? AND listing_url = ?
                """,
                (now, source_platform, url),
            )
            row = connection.execute(
                """
                SELECT * FROM listing_records
                WHERE source_platform = ? AND listing_url = ?
                """,
                (source_platform, url),
            ).fetchone()
        return _listing_from_row(row) if row else None

    def mark_listings_not_seen(
        self,
        community_id: int,
        source_platform: str,
        observed_urls: Iterable[str],
        updated_at: str | None = None,
    ) -> list[ListingRecord]:
        """将本次有效列表中未出现的挂牌记录标记为逻辑删除。

        ``observed_urls`` 为空时直接拒绝，避免一次空页面把整个小区的挂牌
        误判为下架。调用方应在确认本次采集页面有效后再调用本方法。
        """
        if source_platform not in LISTING_PLATFORMS:
            raise ValueError(f"挂牌来源平台不支持: {source_platform}")
        normalized_urls = tuple(
            sorted({normalize_listing_url(url) for url in observed_urls})
        )
        if not normalized_urls:
            raise ValueError("observed_urls 不能为空")
        now = updated_at or _now()
        placeholders = ",".join("?" for _ in normalized_urls)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id FROM listing_records
                WHERE community_id = ? AND source_platform = ?
                  AND is_deleted = 0 AND listing_url NOT IN ({placeholders})
                """,
                (community_id, source_platform, *normalized_urls),
            ).fetchall()
            ids = tuple(int(row["id"]) for row in rows)
            if not ids:
                deleted_rows = []
            else:
                id_placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE listing_records
                    SET is_deleted = 1, updated_at = ?
                    WHERE id IN ({id_placeholders})
                    """,
                    (now, *ids),
                )
                deleted_rows = connection.execute(
                    f"""
                    SELECT * FROM listing_records
                    WHERE id IN ({id_placeholders}) ORDER BY id
                    """,
                    ids,
                ).fetchall()
        return [_listing_from_row(row) for row in deleted_rows]

    def list_deals(
        self,
        community_id: int,
        *,
        source_platform: str | None = None,
        deduplicate: bool = False,
    ) -> list[DealRecord] | list[UniqueDealRecord]:
        """读取成交；deduplicate=True 时按五项事实合并来源。"""
        sql = "SELECT * FROM deal_records WHERE community_id = ?"
        params: list[object] = [community_id]
        if source_platform is not None:
            if source_platform not in DEAL_PLATFORMS:
                raise ValueError(f"成交来源平台不支持: {source_platform}")
            sql += " AND source_platform = ?"
            params.append(source_platform)
        sql += " ORDER BY deal_date DESC, id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        records = [_deal_from_row(row) for row in rows]
        if not deduplicate:
            return records
        return _deduplicate_deals(records)

    def get_community_platform_page(
        self,
        community_id: int,
        source_platform: str,
    ) -> CommunityPlatformPage | None:
        """读取一个小区在指定平台的抓取入口。"""
        if source_platform not in LISTING_PLATFORMS:
            raise ValueError(f"平台页面来源不支持: {source_platform}")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM community_platform_pages
                WHERE community_id = ? AND source_platform = ?
                """,
                (community_id, source_platform),
            ).fetchone()
        return _platform_page_from_row(row) if row is not None else None

    def list_community_platform_pages(
        self,
        community_id: int,
    ) -> list[CommunityPlatformPage]:
        """读取一个小区在各网页平台的抓取入口。"""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM community_platform_pages
                WHERE community_id = ? ORDER BY source_platform
                """,
                (community_id,),
            ).fetchall()
        return [_platform_page_from_row(row) for row in rows]

    def list_listings(
        self,
        community_id: int,
        *,
        include_deleted: bool = False,
        source_platform: str | None = None,
    ) -> list[ListingRecord]:
        sql = "SELECT * FROM listing_records WHERE community_id = ?"
        params: list[object] = [community_id]
        if source_platform is not None:
            if source_platform not in LISTING_PLATFORMS:
                raise ValueError(f"挂牌来源平台不支持: {source_platform}")
            sql += " AND source_platform = ?"
            params.append(source_platform)
        if not include_deleted:
            sql += " AND is_deleted = 0"
        sql += " ORDER BY source_platform, id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_listing_from_row(row) for row in rows]

    def get_platform_freshness(self, community_id: int) -> dict[str, str]:
        """返回各平台最近一次数据更新时间（在架行 MAX(updated_at)）。

        只统计 is_deleted = 0 的行：全部被软删的平台返回空——热路径据此
        视其为冷（NO_DATA 不算热）。update 时间由落库整批刷新，因此该
        MAX 即"小区×平台"的上次采集时间。
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_platform, MAX(updated_at) AS last_update
                FROM listing_records
                WHERE community_id = ? AND is_deleted = 0
                GROUP BY source_platform
                """,
                (int(community_id),),
            ).fetchall()
        return {row["source_platform"]: row["last_update"] for row in rows}

    def list_listing_logs(
        self,
        listing_record_id: int,
        *,
        limit: int | None = None,
    ) -> list[ListingRecordLog]:
        """读取一套挂牌房源的价格快照，按最新采集时间倒序返回。"""
        try:
            normalized_id = int(listing_record_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"listing_record_id 不合法: {listing_record_id}") from exc
        sql = """
            SELECT * FROM listing_record_logs
            WHERE listing_record_id = ?
            ORDER BY observed_at DESC, id DESC
        """
        params: list[object] = [normalized_id]
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit 必须是正整数")
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_listing_log_from_row(row) for row in rows]

    def validate_community(
        self,
        community_id: int,
        city: str,
        administrative_district: str,
    ) -> tuple[str, str]:
        """校验外部传入的小区 ID 与城市、行政区是否一致。

        RPA 批量入库前可先调用此方法，让归属错误在写入任何一行之前失败。
        """
        return self._validate_community(community_id, city, administrative_district)

    def _validate_community(
        self,
        community_id: int,
        city: str,
        administrative_district: str,
    ) -> tuple[str, str]:
        city = _required_text(city, "city")
        administrative_district = _required_text(
            administrative_district, "administrative_district"
        )
        try:
            record = self.community_database.get_by_id(int(community_id))
        except (TypeError, ValueError):
            record = None
        if record is None:
            raise ValueError(f"community_id 不存在: {community_id}")
        if record.city != city or record.administrative_district != administrative_district:
            raise ValueError(
                "community_id 与城市/行政区不匹配: "
                f"community_id={community_id}, city={city}, "
                f"administrative_district={administrative_district}"
            )
        return city, administrative_district


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_observed_at(value: str | None) -> str:
    """规范化采集时间，缺失时使用当前 UTC 时间。"""
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"seen_at 必须是 ISO-8601 时间: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timestamp_is_older(left: str, right: str) -> bool:
    """判断两个 ISO-8601 时间中 left 是否早于 right。"""
    left_time = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
    right_time = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
    if left_time.tzinfo is None:
        left_time = left_time.replace(tzinfo=timezone.utc)
    if right_time.tzinfo is None:
        right_time = right_time.replace(tzinfo=timezone.utc)
    return left_time < right_time


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _optional_text(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _deal_from_row(row: sqlite3.Row) -> DealRecord:
    return DealRecord(
        id=int(row["id"]),
        community_id=int(row["community_id"]),
        city=row["city"],
        administrative_district=row["administrative_district"],
        source_platform=row["source_platform"],
        source_community_name=row["source_community_name"],
        deal_date=row["deal_date"],
        area_sqm=float(row["area_sqm"]),
        total_price_yuan=float(row["total_price_yuan"]),
        unit_price_yuan=float(row["unit_price_yuan"]),
    )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _platform_page_from_row(row: sqlite3.Row) -> CommunityPlatformPage:
    return CommunityPlatformPage(
        id=int(row["id"]),
        community_id=int(row["community_id"]),
        source_platform=row["source_platform"],
        source_community_name=row["source_community_name"],
        listing_page_url=row["listing_page_url"],
        deal_page_url=row["deal_page_url"],
    )


def _listing_from_row(row: sqlite3.Row) -> ListingRecord:
    return ListingRecord(
        id=int(row["id"]),
        community_id=int(row["community_id"]),
        city=row["city"],
        administrative_district=row["administrative_district"],
        source_platform=row["source_platform"],
        listing_url=row["listing_url"],
        source_community_name=row["source_community_name"],
        title=row["title"],
        layout=row["layout"],
        area_sqm=float(row["area_sqm"]) if row["area_sqm"] is not None else None,
        total_price_yuan=(
            float(row["total_price_yuan"])
            if row["total_price_yuan"] is not None
            else None
        ),
        unit_price_yuan=(
            float(row["unit_price_yuan"])
            if row["unit_price_yuan"] is not None
            else None
        ),
        is_deleted=bool(row["is_deleted"]),
        last_seen_at=row["last_seen_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _listing_log_from_row(row: sqlite3.Row) -> ListingRecordLog:
    return ListingRecordLog(
        id=int(row["id"]),
        listing_record_id=int(row["listing_record_id"]),
        observed_at=row["observed_at"],
        total_price_yuan=(
            float(row["total_price_yuan"])
            if row["total_price_yuan"] is not None
            else None
        ),
        unit_price_yuan=(
            float(row["unit_price_yuan"])
            if row["unit_price_yuan"] is not None
            else None
        ),
    )


def _insert_listing_log(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
    """在挂牌当前记录同一事务内追加最终有效的价格快照。"""
    connection.execute(
        """
        INSERT INTO listing_record_logs (
            listing_record_id, observed_at, total_price_yuan, unit_price_yuan
        ) VALUES (?, ?, ?, ?)
        """,
        (
            row["id"],
            row["last_seen_at"],
            row["total_price_yuan"],
            row["unit_price_yuan"],
        ),
    )


def _deduplicate_deals(records: Iterable[DealRecord]) -> list[UniqueDealRecord]:
    grouped: dict[tuple[object, ...], list[DealRecord]] = {}
    for record in records:
        key = (
            record.community_id,
            record.deal_date,
            record.area_sqm,
            record.total_price_yuan,
            record.unit_price_yuan,
        )
        grouped.setdefault(key, []).append(record)
    unique: list[UniqueDealRecord] = []
    for records_for_key in grouped.values():
        first = records_for_key[0]
        unique.append(
            UniqueDealRecord(
                community_id=first.community_id,
                city=first.city,
                administrative_district=first.administrative_district,
                deal_date=first.deal_date,
                area_sqm=first.area_sqm,
                total_price_yuan=first.total_price_yuan,
                unit_price_yuan=first.unit_price_yuan,
                source_platforms=tuple(
                    sorted({record.source_platform for record in records_for_key})
                ),
                source_record_ids=tuple(
                    sorted(record.id for record in records_for_key if record.id is not None)
                ),
            )
        )
    unique.sort(key=lambda record: (record.deal_date, record.source_record_ids), reverse=True)
    return unique
