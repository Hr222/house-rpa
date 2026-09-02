# -*- coding: utf-8 -*-
"""询价编排层的结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from app.algorithm.models import PriceCandidate

if TYPE_CHECKING:
    from app.community_data.models import CommunityRecord
    from app.rpa.core.models import PlatformResult


@dataclass(frozen=True, slots=True)
class ConfirmedCommunityContext:
    """由编排层确认的本次询价小区身份。"""

    community_id: int
    community_group_id: int
    city: str
    administrative_district: str
    canonical_name: str
    aliases: tuple[str, ...]
    phase: Optional[str]
    area: float
    request_id: Optional[str]

    @classmethod
    def from_community_record(
        cls,
        record: "CommunityRecord",
        *,
        area: float,
        request_id: Optional[str],
    ) -> "ConfirmedCommunityContext":
        """从小区主数据记录构造编排层上下文。"""
        return cls(
            community_id=record.community_id,
            community_group_id=record.community_group_id,
            city=record.city,
            administrative_district=record.administrative_district,
            canonical_name=record.name,
            aliases=record.aliases,
            phase=record.phase,
            area=area,
            request_id=request_id,
        )


class InquirySubmissionStatus(str, Enum):
    """创建询价前的小区解析结果。"""

    COMMUNITY_NOT_FOUND = "COMMUNITY_NOT_FOUND"
    COMMUNITY_TYPE_NOT_SUPPORTED = "COMMUNITY_TYPE_NOT_SUPPORTED"
    COMMUNITY_PHASE_REQUIRED = "COMMUNITY_PHASE_REQUIRED"
    ACCEPTED = "ACCEPTED"


@dataclass(frozen=True, slots=True)
class CommunityCandidate:
    """需要调用方明确期数时返回的候选小区摘要。"""

    community_group_id: int
    community_id: int
    canonical_name: str
    phase: Optional[str]
    aliases: tuple[str, ...]
    city: str
    administrative_district: str

    @classmethod
    def from_community_record(cls, record: "CommunityRecord") -> "CommunityCandidate":
        """从小区主数据记录生成对外候选摘要。"""
        return cls(
            community_group_id=record.community_group_id,
            community_id=record.community_id,
            canonical_name=record.name,
            phase=record.phase,
            aliases=record.aliases,
            city=record.city,
            administrative_district=record.administrative_district,
        )


@dataclass(frozen=True, slots=True)
class InquirySubmission:
    """编排层返回给 HTTP 入口的任务准入结果。"""

    status: InquirySubmissionStatus
    task: Optional[dict] = None
    candidates: tuple[CommunityCandidate, ...] = ()


@dataclass(slots=True)
class InquiryResult:
    """编排层基于原始采集结果生成的最终询价结果。"""

    success: bool
    final_price: Optional[float] = None
    branch: str = "FAILED"
    note: Optional[str] = None
    quote_avg: Optional[float] = None
    deal_avg: Optional[float] = None
    platform_results: list["PlatformResult"] = field(default_factory=list)
    candidates: list[PriceCandidate] = field(default_factory=list)
    reference_code: Optional[str] = None
    reference_area_tolerance: Optional[float] = None
    reference_area_min: Optional[float] = None
    reference_area_max: Optional[float] = None
    reference_listing_count: Optional[int] = None


@dataclass(frozen=True, slots=True)
class InquiryCompletionPayload:
    """编排层交给 Runtime 的对外任务结果和回调负载。"""

    task_result: dict
    callback_payload: dict
