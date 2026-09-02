# -*- coding: utf-8 -*-
"""RPA 平台采集服务。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Iterable, Optional

from app.rpa.core.models import (
    InquiryRequest,
    PlatformResult,
    PlatformSession,
    RPACollectionResult,
)
from app.rpa.core.status import PlatformResultStatus
from app.rpa.platforms.base import (
    PlatformAdapter,
    manual_verify_events_snapshot,
    manual_verify_waiting_snapshot,
    reset_manual_verify_events,
)


log = logging.getLogger(__name__)


class RPAInquiryService:
    """管理平台常驻会话，只返回原始采集结果。"""

    def __init__(self, browsers: dict, adapters: Iterable[PlatformAdapter]):
        self.browsers = browsers
        self.adapters = list(adapters)
        self.sessions: dict[str, PlatformSession] = {}

    async def start(self):
        for adapter in self.adapters:
            browser = self.browsers.get(adapter.code)
            session = await adapter.open_session(browser, new_tab=False)
            self.sessions[adapter.code] = session
            log.info("platform ready: %s -> %s", adapter.name, session.start_url)
        return self.sessions

    def list_sessions(self) -> list[PlatformSession]:
        return list(self.sessions.values())

    async def run_inquiry(
        self,
        request: InquiryRequest,
        platform_codes: Optional[list[str]] = None,
        before_collection_complete: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> RPACollectionResult:
        """只做平台采集，不做价格聚合与最终决策。"""
        reset_manual_verify_events()
        log.info(
            "查询城市: %s, 小区: %s, 面积: %.1f㎡",
            request.city,
            request.community_name,
            request.area,
        )
        if platform_codes is None:
            adapters = self.adapters
        else:
            codes = set(platform_codes)
            adapters = [adapter for adapter in self.adapters if adapter.code in codes]

        async def collect_one(adapter: PlatformAdapter) -> PlatformResult:
            session = self.sessions[adapter.code]
            browser = self.browsers.get(adapter.code)
            started_at = time.monotonic()
            log.info(
                "[平台采集开始] %s(%s) request_id=%s",
                adapter.name,
                adapter.code,
                request.request_id or "-",
            )
            try:
                if session.page is not None:
                    await session.page.activate()
                result = await adapter.collect(browser, session, request)
                log.info(
                    "[平台采集结束] %s(%s) request_id=%s status=%s elapsed=%.1fs reason=%s",
                    adapter.name,
                    adapter.code,
                    request.request_id or "-",
                    result.status,
                    time.monotonic() - started_at,
                    result.reason or "-",
                )
                return result
            except Exception as exc:
                log.exception(
                    "[平台采集异常] %s(%s) request_id=%s elapsed=%.1fs",
                    adapter.name,
                    adapter.code,
                    request.request_id or "-",
                    time.monotonic() - started_at,
                )
                return PlatformResult(
                    name=adapter.name,
                    status=PlatformResultStatus.ERROR,
                    reason=str(exc),
                    request_id=request.request_id,
                )

        platform_results = await asyncio.gather(
            *[collect_one(adapter) for adapter in adapters]
        )

        waiting = manual_verify_waiting_snapshot()
        if waiting:
            log.warning("采集完成后仍有平台等待人工风控处理: %s", ", ".join(waiting))
            while waiting:
                await asyncio.sleep(0.2)
                waiting = manual_verify_waiting_snapshot()

        if before_collection_complete is not None:
            await before_collection_complete()

        risk_events = manual_verify_events_snapshot()
        if risk_events:
            summary = "; ".join(f"{context}={status}" for context, status in risk_events)
            log.warning("[本次询价风控汇总] request_id=%s: %s", request.request_id, summary)

        log.info("所有平台采集协程已完成，准备向编排层交付原始结果")
        return RPACollectionResult(platform_results=platform_results)
