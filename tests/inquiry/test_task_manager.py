# -*- coding: utf-8 -*-
"""询价任务快照与 RPA 终态通知边界测试。"""

from __future__ import annotations

import asyncio

import pytest

from app.inquiry.completion import InquiryCompletionOrchestrator
from app.inquiry.models import ConfirmedCommunityContext
from app.inquiry.task_manager import InquiryTaskManager
from app.inquiry.task_store import InquiryTaskSnapshot, load_pending_tasks, save_pending_task
from app.rpa.core.models import InquiryRequest
from app.rpa.core.status import TaskStatus
from tests.inquiry.helpers import community


class CapturingRuntime:
    """不执行浏览器操作，只记录交给 RPA 的采集请求。"""

    def __init__(self) -> None:
        self.calls = []

    def is_ready(self) -> bool:
        return True

    async def enqueue_inquiry(
        self,
        request,
        *,
        completion_handler=None,
        terminal_handler=None,
    ):
        self.calls.append((request, completion_handler, terminal_handler))
        return {
            "taskId": request.request_id,
            "status": "排队中",
            "statusCode": "QUEUED",
        }


def _context(*, request_id: str = "order-001") -> ConfirmedCommunityContext:
    record = community(community_id=123, community_group_id=45)
    return ConfirmedCommunityContext.from_community_record(
        record,
        area=89.5,
        request_id=request_id,
    )


@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.FAILED])
def test_confirmed_community_snapshot_is_owned_by_inquiry_layer(monkeypatch, tmp_path, status) -> None:
    async def exercise() -> None:
        runtime = CapturingRuntime()
        manager = InquiryTaskManager(runtime, InquiryCompletionOrchestrator())
        await manager.start()

        task = await manager.submit(_context())

        snapshots = load_pending_tasks()
        assert len(snapshots) == 1
        assert snapshots[0].task_id == task["taskId"]
        assert snapshots[0].community_id == 123
        assert snapshots[0].confirmed_community.community_id == 123
        assert snapshots[0].confirmed_community.community_group_id == 45
        assert snapshots[0].request.community_name == "示例花园一期"
        assert not hasattr(runtime.calls[0][0], "community_id")

        terminal_handler = runtime.calls[0][2]
        assert callable(terminal_handler)
        await terminal_handler(task["taskId"], status)
        assert load_pending_tasks() == []
        await manager.stop()

    monkeypatch.setattr("app.inquiry.task_store.INQUIRY_PERSIST_DIR", tmp_path)
    asyncio.run(exercise())


def test_recovery_rebuilds_completion_handler_from_persisted_context(monkeypatch, tmp_path) -> None:
    async def exercise() -> None:
        context = _context(request_id="recovery-001")
        snapshot = InquiryTaskSnapshot(
            task_id="recovery-001",
            community_id=context.community_id,
            created_at=100.0,
            request=InquiryRequest(
                community_name=context.canonical_name,
                area=context.area,
                city=context.city,
                administrative_district=context.administrative_district,
                request_id="recovery-001",
            ),
            confirmed_community=context,
        )
        save_pending_task(snapshot)

        runtime = CapturingRuntime()
        manager = InquiryTaskManager(runtime, InquiryCompletionOrchestrator())
        await manager.start()
        await asyncio.wait_for(manager._recovery_complete.wait(), timeout=1)

        assert manager.recovery_complete
        assert len(runtime.calls) == 1
        request, completion_handler, terminal_handler = runtime.calls[0]
        assert request.request_id == "recovery-001"
        assert request.community_name == context.canonical_name
        assert callable(completion_handler)
        assert callable(terminal_handler)
        assert load_pending_tasks()[0].confirmed_community.community_id == 123
        await manager.stop()

    monkeypatch.setattr("app.inquiry.task_store.INQUIRY_PERSIST_DIR", tmp_path)
    asyncio.run(exercise())
