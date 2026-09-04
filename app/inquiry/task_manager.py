# -*- coding: utf-8 -*-
"""询价任务生命周期编排。

本模块在确认 ``community_id`` 后创建任务快照，并负责崩溃恢复和
终态清理。RPA Runtime 只执行内存中的采集任务。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import replace
from typing import Callable, Optional

from app.inquiry.completion import InquiryCompletionOrchestrator
from app.inquiry.models import ConfirmedCommunityContext
from app.inquiry.task_store import (
    InquiryTaskSnapshot,
    delete_pending_task,
    load_pending_tasks,
    save_pending_task,
)
from app.rpa.core.models import InquiryRequest
from app.rpa.core.status import TaskStatus
from app.rpa.runtime import RPARuntime


log = logging.getLogger(__name__)


class InquiryTaskManager:
    """管理确认小区后的询价快照与恢复过程。"""

    def __init__(
        self,
        runtime: RPARuntime,
        completion_orchestrator: InquiryCompletionOrchestrator,
        platform_entry_loader: Optional[Callable[[int], tuple[dict, dict]]] = None,
    ) -> None:
        self.runtime = runtime
        self.completion_orchestrator = completion_orchestrator
        self._platform_entry_loader = platform_entry_loader
        self._started = False
        self._recovery_complete = asyncio.Event()
        self._recovery_task: Optional[asyncio.Task] = None

    def _resolve_platform_entries(self, community_id: int) -> tuple[dict, dict]:
        """按社区查各平台挂牌/成交入口（URL 白名单直达，方案A）。

        返回 (platform_listing_pages, platform_deal_pages)：code -> 入口 URL。
        未初始化入口的平台不在结果中，对应平台采集时直接 NO_DATA。
        """
        if self._platform_entry_loader is not None:
            return self._platform_entry_loader(community_id)
        from app.rpa.registry import build_default_adapters
        from app.property_records.database import PropertyRecordsDatabase

        database = PropertyRecordsDatabase()
        listing_pages: dict[str, str] = {}
        deal_pages: dict[str, str] = {}
        for adapter in build_default_adapters():
            code = adapter.code
            try:
                page = database.get_community_platform_page(community_id, code)
            except Exception as exc:
                log.warning("读取平台入口失败 code=%s community_id=%s: %s", code, community_id, exc)
                continue
            if page is None:
                continue
            if page.listing_page_url:
                listing_pages[code] = page.listing_page_url
            if page.deal_page_url:
                deal_pages[code] = page.deal_page_url
        return listing_pages, deal_pages

    @property
    def recovery_complete(self) -> bool:
        """是否已完成残留任务的恢复入队。"""
        return self._recovery_complete.is_set()

    async def start(self) -> None:
        """读取快照，并在 RPA 重新就绪后恢复残留任务。"""
        if self._started:
            return
        self._started = True
        self._recovery_complete = asyncio.Event()
        snapshots = load_pending_tasks()
        if not snapshots:
            self._recovery_complete.set()
            return
        self._recovery_task = asyncio.create_task(
            self._restore_when_runtime_ready(snapshots),
            name="inquiry-task-recovery",
        )

    async def stop(self) -> None:
        """停止恢复协程；未完成任务快照保留给下次进程启动。"""
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task
        self._recovery_task = None
        self._started = False

    async def submit(self, context: ConfirmedCommunityContext) -> dict:
        """持久化完整上下文后，将纯采集请求交给 RPA Runtime。"""
        if not self._started:
            raise RuntimeError("INQUIRY_TASK_MANAGER_NOT_STARTED")
        if not self.recovery_complete:
            raise RuntimeError("INQUIRY_RECOVERY_PENDING")

        task_id = context.request_id or uuid.uuid4().hex
        task_context = replace(context, request_id=task_id)
        listing_pages, deal_pages = self._resolve_platform_entries(task_context.community_id)
        request = InquiryRequest(
            community_name=task_context.canonical_name,
            area=task_context.area,
            city=task_context.city,
            administrative_district=task_context.administrative_district,
            request_id=task_id,
            platform_listing_pages=listing_pages,
            platform_deal_pages=deal_pages,
        )
        snapshot = InquiryTaskSnapshot(
            task_id=task_id,
            community_id=task_context.community_id,
            created_at=time.time(),
            request=request,
            confirmed_community=task_context,
        )
        save_pending_task(snapshot)
        try:
            return await self._enqueue_snapshot(snapshot)
        except Exception:
            delete_pending_task(task_id)
            raise

    async def _restore_when_runtime_ready(
        self,
        snapshots: list[InquiryTaskSnapshot],
    ) -> None:
        """待 RPA 就绪后按快照创建时间重建任务，并阻止新任务插队。"""
        for snapshot in snapshots:
            while True:
                await self._wait_for_runtime_ready()
                try:
                    await self._enqueue_snapshot(snapshot)
                    break
                except RuntimeError as exc:
                    if str(exc) != "SERVICE_NOT_READY":
                        log.exception("恢复询价任务失败: task_id=%s", snapshot.task_id)
                        break
                    await asyncio.sleep(1)
                except Exception:
                    log.exception("恢复询价任务失败: task_id=%s", snapshot.task_id)
                    break
        self._recovery_complete.set()
        log.info("询价任务恢复入队完成: count=%d", len(snapshots))

    async def _wait_for_runtime_ready(self) -> None:
        while not self.runtime.is_ready():
            await asyncio.sleep(1)

    async def _enqueue_snapshot(self, snapshot: InquiryTaskSnapshot) -> dict:
        return await self.runtime.enqueue_inquiry(
            replace(snapshot.request),
            completion_handler=self.completion_orchestrator.handler_for(
                snapshot.confirmed_community,
            ),
            terminal_handler=self._handle_runtime_terminal,
        )

    async def _handle_runtime_terminal(
        self,
        task_id: str,
        status: TaskStatus,
    ) -> None:
        """RPA 明确结束后才删除快照；取消中的任务继续保留。"""
        if status not in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            return
        delete_pending_task(task_id)
