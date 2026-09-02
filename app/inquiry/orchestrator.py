# -*- coding: utf-8 -*-
"""小区身份确认与 RPA 任务创建的独立编排层。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

from app.community_data import resolve_communities
from app.community_data.models import CommunityRecord, EstateType
from app.inquiry.models import (
    CommunityCandidate,
    ConfirmedCommunityContext,
    InquirySubmission,
    InquirySubmissionStatus,
)
from app.inquiry.task_manager import InquiryTaskManager


log = logging.getLogger(__name__)
CommunityResolver = Callable[[str, str, str, Optional[str]], list[CommunityRecord]]


class InquiryOrchestrator:
    """确认小区身份后才将询价请求交给 RPA 运行时。"""

    def __init__(
        self,
        task_manager: InquiryTaskManager,
        community_resolver: CommunityResolver = resolve_communities,
    ) -> None:
        self.task_manager = task_manager
        self.community_resolver = community_resolver

    async def submit(
        self,
        *,
        city: str,
        administrative_district: str,
        community_name: str,
        area: float,
        request_id: Optional[str],
    ) -> InquirySubmission:
        """按小区查询结果返回业务结果或创建已确认身份的 RPA 任务。"""
        records = self.community_resolver(city, administrative_district, community_name, None)
        residential = [
            record
            for record in records
            if record.estate_type == EstateType.RESIDENTIAL.value
        ]
        if not records:
            log.info(
                "询价小区未找到: city=%s district=%s community=%s request_id=%s",
                city,
                administrative_district,
                community_name,
                request_id or "-",
            )
            return InquirySubmission(InquirySubmissionStatus.COMMUNITY_NOT_FOUND)

        if not residential:
            candidates = tuple(
                CommunityCandidate.from_community_record(record) for record in records
            )
            log.info(
                "询价小区类型不支持: city=%s district=%s community=%s candidates=%s request_id=%s",
                city,
                administrative_district,
                community_name,
                [candidate.canonical_name for candidate in candidates],
                request_id or "-",
            )
            return InquirySubmission(
                InquirySubmissionStatus.COMMUNITY_TYPE_NOT_SUPPORTED,
                candidates=candidates,
            )

        records = residential
        if len(records) != 1:
            candidates = tuple(CommunityCandidate.from_community_record(record) for record in records)
            log.info(
                "询价小区期数未明确: city=%s district=%s community=%s candidates=%s request_id=%s",
                city,
                administrative_district,
                community_name,
                [candidate.canonical_name for candidate in candidates],
                request_id or "-",
            )
            return InquirySubmission(
                InquirySubmissionStatus.COMMUNITY_PHASE_REQUIRED,
                candidates=candidates,
            )

        context = ConfirmedCommunityContext.from_community_record(
            records[0],
            area=area,
            request_id=request_id,
        )
        task = await self.task_manager.submit(context)
        log.info(
            "询价任务已创建: task_id=%s community_id=%s community=%s",
            task["taskId"],
            context.community_id,
            context.canonical_name,
        )
        return InquirySubmission(InquirySubmissionStatus.ACCEPTED, task=task)
