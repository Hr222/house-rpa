# -*- coding: utf-8 -*-
"""小区基础数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


GEOCODE_PENDING = "PENDING"
GEOCODE_SUCCESS = "SUCCESS"
GEOCODE_FAILED = "FAILED"
COORDINATE_SYSTEM = "GCJ-02"


@dataclass(frozen=True)
class CommunitySeed:
    """写入数据库所需的小区基础字段。"""

    city: str
    administrative_district: str
    name: str
    district: Optional[str] = None
    aliases: tuple[str, ...] = ()
    build_year: Optional[int] = None
    remark: Optional[str] = None


@dataclass(frozen=True)
class CommunityRecord:
    """对外返回的小区记录。"""

    community_id: int
    community_group_id: int
    city: str
    administrative_district: str
    district: Optional[str]
    name: str
    phase: Optional[str]
    build_year: Optional[int]
    aliases: tuple[str, ...]
    address: str
    longitude: Optional[float]
    latitude: Optional[float]
    coordinate_system: Optional[str]
    geocode_status: str
    geocode_level: Optional[int]
    geocode_reliability: Optional[int]
    remark: Optional[str]
    created_at: str
    updated_at: str
    distance_meters: Optional[float] = None
