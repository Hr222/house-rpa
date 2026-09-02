# -*- coding: utf-8 -*-
"""询价编排层的关键准入测试。"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from app.inquiry.models import InquirySubmissionStatus
from app.inquiry.orchestrator import InquiryOrchestrator
from tests.inquiry.helpers import FakeTaskManager, community


def test_not_found_does_not_enqueue_task() -> None:
    task_manager = FakeTaskManager()
    orchestrator = InquiryOrchestrator(task_manager, community_resolver=lambda *_args: [])

    result = asyncio.run(
        orchestrator.submit(
            city="深圳",
            administrative_district="南山区",
            community_name="不存在小区",
            area=89.5,
            request_id="order-001",
        )
    )

    assert result.status == InquirySubmissionStatus.COMMUNITY_NOT_FOUND
    assert task_manager.calls == []


def test_non_residential_returns_type_not_supported_without_enqueue() -> None:
    task_manager = FakeTaskManager()
    record = community(name="大悦城商业中心", phase=None, estate_type="非住宅")
    orchestrator = InquiryOrchestrator(task_manager, community_resolver=lambda *_args: [record])

    result = asyncio.run(
        orchestrator.submit(
            city="深圳",
            administrative_district="宝安区",
            community_name="大悦城商业中心",
            area=89.5,
            request_id="order-001",
        )
    )

    assert result.status == InquirySubmissionStatus.COMMUNITY_TYPE_NOT_SUPPORTED
    assert [item.canonical_name for item in result.candidates] == ["大悦城商业中心"]
    assert task_manager.calls == []


def test_mixed_estate_types_proceeds_with_residential_only() -> None:
    task_manager = FakeTaskManager()
    apartment = community(community_id=2, name="示例花园公寓", phase=None, estate_type="公寓")
    residential = community(name="示例花园一期")
    orchestrator = InquiryOrchestrator(
        task_manager,
        community_resolver=lambda *_args: [apartment, residential],
    )

    result = asyncio.run(
        orchestrator.submit(
            city="深圳",
            administrative_district="南山区",
            community_name="示例花园",
            area=89.5,
            request_id="order-001",
        )
    )

    assert result.status == InquirySubmissionStatus.ACCEPTED
    assert task_manager.calls[0].community_id == residential.community_id


def test_multiple_phases_returns_candidates_without_enqueue() -> None:
    task_manager = FakeTaskManager()
    first = community()
    second = replace(first, community_id=2, name="示例花园二期", phase="二期")
    orchestrator = InquiryOrchestrator(task_manager, community_resolver=lambda *_args: [first, second])

    result = asyncio.run(
        orchestrator.submit(
            city="深圳",
            administrative_district="南山区",
            community_name="示例花园",
            area=89.5,
            request_id=None,
        )
    )

    assert result.status == InquirySubmissionStatus.COMMUNITY_PHASE_REQUIRED
    assert [item.canonical_name for item in result.candidates] == ["示例花园一期", "示例花园二期"]
    assert task_manager.calls == []


def test_unique_community_keeps_context_in_orchestrator_and_enqueues_canonical_name() -> None:
    task_manager = FakeTaskManager()
    record = community()
    orchestrator = InquiryOrchestrator(task_manager, community_resolver=lambda *_args: [record])

    result = asyncio.run(
        orchestrator.submit(
            city="深圳",
            administrative_district="南山区",
            community_name="示例花园1期",
            area=89.5,
            request_id="order-001",
        )
    )

    assert result.status == InquirySubmissionStatus.ACCEPTED
    assert result.task is not None
    context = task_manager.calls[0]
    assert context.community_id == record.community_id
    assert context.canonical_name == "示例花园一期"
    assert context.administrative_district == "南山区"
