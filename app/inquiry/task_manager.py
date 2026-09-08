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
    clear_pending_tasks,
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
        recover_pending: bool = True,
    ) -> None:
        self.runtime = runtime
        self.completion_orchestrator = completion_orchestrator
        self._platform_entry_loader = platform_entry_loader
        self._recover_pending = recover_pending
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
        if not self._recover_pending:
            # 测试模式：不恢复历史任务，直接清空残留快照，保持启动干净
            clear_pending_tasks()
            self._recovery_complete.set()
            return
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

        # request_id 是客户端传入的业务标识；task_id 是服务端内部生命周期标识。
        # 两者分离后，客户端值不会再参与快照文件名或 Runtime 队列键生成。
        task_id = uuid.uuid4().hex
        task_context = context
        listing_pages, deal_pages = self._resolve_platform_entries(task_context.community_id)
        hot_platforms = self._select_hot_platforms(
            task_context.community_id, sorted(listing_pages.keys())
        )
        request = InquiryRequest(
            community_name=task_context.canonical_name,
            area=task_context.area,
            city=task_context.city,
            administrative_district=task_context.administrative_district,
            request_id=task_context.request_id,
            platform_listing_pages=listing_pages,
            platform_deal_pages=deal_pages,
            platform_codes=self._cold_platform_codes(hot_platforms),
        )
        snapshot = InquiryTaskSnapshot(
            task_id=task_id,
            community_id=task_context.community_id,
            created_at=time.time(),
            request=request,
            confirmed_community=task_context,
            hot_platforms=tuple(hot_platforms),
        )
        save_pending_task(snapshot)
        try:
            return await self._enqueue_snapshot(snapshot)
        except Exception:
            delete_pending_task(task_id)
            raise

    def _select_hot_platforms(self, community_id: int, entry_codes: list[str]) -> list[str]:
        """按 listing_records.updated_at 判定热平台；任何异常一律视为全冷（宁慢不错）。"""
        if not entry_codes:
            return []
        try:
            from app.property_records.database import PropertyRecordsDatabase

            freshness = PropertyRecordsDatabase().get_platform_freshness(community_id)
        except Exception:
            log.warning(
                "热数据查询失败，全部平台走 RPA：community_id=%s", community_id, exc_info=True
            )
            return []
        from app.inquiry.hot_data import select_hot_platforms

        hot = select_hot_platforms(freshness, entry_codes)
        if hot:
            log.info(
                "热数据命中：community_id=%s 平台=%s（其余走 RPA）",
                community_id,
                hot,
            )
        return hot

    @staticmethod
    def _cold_platform_codes(hot_platforms: list[str]) -> list[str]:
        """全部注册平台减去热平台，即本次 RPA 需要采集的冷平台。"""
        from app.rpa.registry import build_default_adapters

        hot = set(hot_platforms)
        return [adapter.code for adapter in build_default_adapters() if adapter.code not in hot]

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
            task_id=snapshot.task_id,
            completion_handler=self.completion_orchestrator.handler_for(
                snapshot.confirmed_community,
                hot_platform_codes=snapshot.hot_platforms,
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
