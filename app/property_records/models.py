# -*- coding: utf-8 -*-
"""房源记录模块的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class DealRecord:
    """一条已经标准化的成交事实。"""

    id: Optional[int]
    community_id: int
    city: str
    administrative_district: str
    source_platform: str
    source_community_name: str
    deal_date: str
    area_sqm: float
    total_price_yuan: float
    unit_price_yuan: float


@dataclass(frozen=True, slots=True)
class CommunityDealPage:
    """一个小区在某来源平台上的成交列表入口。"""

    id: Optional[int]
    community_id: int
    city: str
    administrative_district: str
    source_platform: str
    source_community_name: str
    deal_page_url: str


@dataclass(frozen=True, slots=True)
class CommunityListingPage:
    """一个小区在某来源平台上的挂牌列表入口。"""

    id: Optional[int]
    community_id: int
    source_platform: str
    listing_page_url: str


@dataclass(frozen=True, slots=True)
class ListingRecord:
    """一套挂牌房源的当前状态。"""

    id: Optional[int]
    community_id: int
    city: str
    administrative_district: str
    source_platform: str
    listing_url: str
    source_community_name: str
    title: Optional[str]
    layout: Optional[str]
    area_sqm: Optional[float]
    total_price_yuan: Optional[float]
    unit_price_yuan: Optional[float]
    is_deleted: bool
    last_seen_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ListingRecordLog:
    """一套挂牌房源的一次有效价格快照。"""

    id: Optional[int]
    listing_record_id: int
    observed_at: str
    total_price_yuan: Optional[float]
    unit_price_yuan: Optional[float]


@dataclass(frozen=True, slots=True)
class UniqueDealRecord:
    """出库时按五项成交事实合并后的记录。"""

    community_id: int
    city: str
    administrative_district: str
    deal_date: str
    area_sqm: float
    total_price_yuan: float
    unit_price_yuan: float
    source_platforms: tuple[str, ...]
    source_record_ids: tuple[int, ...]
