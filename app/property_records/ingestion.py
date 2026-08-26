# -*- coding: utf-8 -*-
"""将 RPA 已解析的成交和挂牌结果写入房源记录模块。"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.property_records.database import PropertyRecordsDatabase
from app.property_records.models import (
    CommunityDealPage,
    CommunityListingPage,
    DealRecord,
    ListingRecord,
)


log = logging.getLogger(__name__)

_SOURCE_PLATFORM_ALIASES = {
    "lj": "lj",
    "链家": "lj",
    "fang": "fang",
    "房天下": "fang",
    "ke": "ke",
    "贝壳": "ke",
    "ajk": "ajk",
    "安居客": "ajk",
    "lyj": "lyj",
    "乐有家": "lyj",
}


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """一次平台结果入库的结果和被跳过的行。"""

    deals: tuple[DealRecord, ...] = ()
    listings: tuple[ListingRecord, ...] = ()
    deal_page: CommunityDealPage | None = None
    listing_page: CommunityListingPage | None = None
    skipped_deals: tuple[str, ...] = ()
    skipped_listings: tuple[str, ...] = ()


class PropertyRecordsIngestion:
    """把平台采集结果转换成 property_records 的标准字段。

    该类只负责入库编排，不负责小区名称解析。调用方必须先通过
    community 模块取得并确认 ``community_id``，之后本类仍会再次校验城市和
    行政区，避免错误绑定。
    """

    def __init__(self, database: PropertyRecordsDatabase | None = None) -> None:
        self.database = database or PropertyRecordsDatabase()

    def ingest_rpa_result(
        self,
        *,
        community_id: int,
        city: str,
        administrative_district: str,
        source_community_name: str,
        result: Any,
        deal_page_url: str | None = None,
        listing_page_url: str | None = None,
        listing_records: Iterable[Mapping[str, Any]] = (),
        seen_at: str | None = None,
    ) -> IngestionReport:
        """直接接收 RPA ``PlatformResult``，保存其中可用的真实成交记录。

        当前 RPA 的安居客、乐有家结果会用均价顶替 ``deal_prices``，没有
        ``deal_records`` 明细；本方法只读取明细列表，不会把均价顶替值写入
        成交事实表。挂牌地址由调用方通过 ``listing_records`` 提供，因为
        平台快照是否包含详情页 URL 是各适配器的页面数据问题。
        """
        source_platform = normalize_source_platform(
            getattr(result, "source_platform", None) or getattr(result, "name", "")
        )
        return self.ingest_platform_result(
            community_id=community_id,
            city=city,
            administrative_district=administrative_district,
            source_platform=source_platform,
            source_community_name=source_community_name,
            deal_records=getattr(result, "deal_records", ()) or (),
            deal_page_url=deal_page_url,
            listing_page_url=listing_page_url,
            listing_records=listing_records,
            seen_at=seen_at,
        )

    def ingest_platform_result(
        self,
        *,
        community_id: int,
        city: str,
        administrative_district: str,
        source_platform: str,
        source_community_name: str,
        deal_records: Iterable[Mapping[str, Any]] = (),
        deal_page_url: str | None = None,
        listing_page_url: str | None = None,
        listing_records: Iterable[Mapping[str, Any]] = (),
        seen_at: str | None = None,
    ) -> IngestionReport:
        """写入一批平台结果。

        成交行支持 RPA 当前使用的键名：``area``、``date``、``total_price``
        （单位为万）、``price``（元/平方米）。也接受已经标准化的
        ``area_sqm``、``deal_date``、``total_price_yuan``、
        ``unit_price_yuan``。挂牌行必须提供 ``listing_url``，其余价格和面积
        字段缺失时按挂牌表的可空规则保存。
        """
        platform = normalize_source_platform(source_platform)
        self.database.validate_community(
            community_id,
            city,
            administrative_district,
        )
        deals: list[DealRecord] = []
        skipped_deals: list[str] = []
        for index, row in enumerate(deal_records):
            try:
                deal = self.database.upsert_deal(
                    community_id=community_id,
                    city=city,
                    administrative_district=administrative_district,
                    source_platform=platform,
                    source_community_name=source_community_name,
                    deal_date=_required_value(row, "deal_date", "date"),
                    area_sqm=_required_value(row, "area_sqm", "area"),
                    total_price=_deal_total_price(row),
                    unit_price_yuan=_required_value(
                        row,
                        "unit_price_yuan",
                        "unit_price",
                        "price",
                    ),
                )
            except (TypeError, ValueError, KeyError) as exc:
                reason = f"第 {index + 1} 条成交记录未入库: {exc}"
                log.warning(reason)
                skipped_deals.append(reason)
                continue
            deals.append(deal)

        page = None
        if deal_page_url is not None:
            page = self.database.upsert_deal_page(
                community_id=community_id,
                city=city,
                administrative_district=administrative_district,
                source_platform=platform,
                source_community_name=source_community_name,
                deal_page_url=deal_page_url,
            )

        listing_page = None
        if listing_page_url is not None:
            listing_page = self.database.upsert_listing_page(
                community_id=community_id,
                source_platform=platform,
                listing_page_url=listing_page_url,
            )

        listings: list[ListingRecord] = []
        skipped_listings: list[str] = []
        for index, row in enumerate(listing_records):
            try:
                listing = self.database.upsert_listing(
                    community_id=community_id,
                    city=city,
                    administrative_district=administrative_district,
                    source_platform=platform,
                    listing_url=_required_value(row, "listing_url", "url"),
                    source_community_name=source_community_name,
                    title=_optional_value(row, "title"),
                    layout=_optional_value(row, "layout"),
                    area_sqm=_optional_value(row, "area_sqm", "area"),
                    total_price=_listing_total_price(row),
                    unit_price_yuan=_optional_value(
                        row,
                        "unit_price_yuan",
                        "unit_price",
                        "price",
                    ),
                    seen_at=seen_at,
                )
            except (TypeError, ValueError, KeyError) as exc:
                reason = f"第 {index + 1} 条挂牌记录未入库: {exc}"
                log.warning(reason)
                skipped_listings.append(reason)
                continue
            listings.append(listing)

        return IngestionReport(
            deals=tuple(deals),
            listings=tuple(listings),
            deal_page=page,
            listing_page=listing_page,
            skipped_deals=tuple(skipped_deals),
            skipped_listings=tuple(skipped_listings),
        )


def normalize_source_platform(value: str) -> str:
    """将平台代码或中文展示名转换为数据库平台代码。"""
    key = str(value or "").strip().lower()
    platform = _SOURCE_PLATFORM_ALIASES.get(key)
    if platform is None:
        raise ValueError(f"来源平台不支持: {value}")
    return platform


def _required_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return value
    raise ValueError(f"缺少字段: {' / '.join(names)}")


def _optional_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return value
    return None


def _deal_total_price(row: Mapping[str, Any]) -> Any:
    value = _required_value(row, "total_price_yuan", "total_price_wan", "total_price")
    if row.get("total_price_yuan") is not None:
        return value
    if row.get("total_price_wan") is not None:
        return f"{value}万"
    unit = str(row.get("total_price_unit") or "万").strip().lower()
    return f"{value}万" if unit in {"万", "wan", "10k"} else value


def _listing_total_price(row: Mapping[str, Any]) -> Any:
    value = _optional_value(row, "total_price_yuan", "total_price_wan", "total_price")
    if value is None or row.get("total_price_yuan") is not None:
        return value
    if row.get("total_price_wan") is not None:
        return f"{value}万"
    unit = str(row.get("total_price_unit") or "万").strip().lower()
    return f"{value}万" if unit in {"万", "wan", "10k"} else value


__all__ = ["IngestionReport", "PropertyRecordsIngestion", "normalize_source_platform"]
