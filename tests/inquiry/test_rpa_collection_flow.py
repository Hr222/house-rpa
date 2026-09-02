# -*- coding: utf-8 -*-
"""RPA collection boundary tests."""

from __future__ import annotations

import asyncio
import contextlib

from app.inquiry.models import InquiryCompletionPayload
from app.rpa.core.models import (
    InquiryRequest,
    ListingSnapshot,
    PlatformResult,
    PlatformSession,
    RPACollectionResult,
)
from app.rpa.core.status import PlatformResultStatus, ServiceStatus, TaskStatus
from app.rpa.runtime import RPARuntime
from app.rpa.service import RPAInquiryService


class CapturingPlatformAdapter:
    """Minimal shell that records the pure collection input."""

    code = "test"
    name = "测试平台"

    def __init__(self) -> None:
        self.request: InquiryRequest | None = None

    async def collect(
        self,
        browser,
        session: PlatformSession,
        request: InquiryRequest,
    ) -> PlatformResult:
        self.request = request
        return PlatformResult(
            name=self.name,
            status=PlatformResultStatus.SUCCESS,
            quote_prices=[100000.0],
            listing_snapshots=[
                ListingSnapshot(house_id="house-1", area=89.5, unit_price=100000.0)
            ],
            request_id=request.request_id,
        )


def test_service_returns_raw_collection_without_orchestration_context() -> None:
    adapter = CapturingPlatformAdapter()
    service = RPAInquiryService(browsers={adapter.code: None}, adapters=[adapter])
    service.sessions[adapter.code] = PlatformSession(
        code=adapter.code,
        name=adapter.name,
        start_url="https://example.test/",
        page=None,
        ready=True,
    )
    request = InquiryRequest(
        community_name="示例花园一期",
        area=89.5,
        city="深圳",
        administrative_district="南山区",
        request_id="collection-001",
    )

    result = asyncio.run(service.run_inquiry(request))

    assert isinstance(result, RPACollectionResult)
    assert adapter.request is request
    assert result.platform_results[0].listing_snapshots[0].house_id == "house-1"


def test_runtime_hands_raw_collection_to_injected_completion_handler() -> None:
    async def run() -> tuple[RPACollectionResult, dict, list[tuple[str, TaskStatus]]]:
        adapter = CapturingPlatformAdapter()
        received: RPACollectionResult | None = None
        terminal_events: list[tuple[str, TaskStatus]] = []

        async def complete(
            request: InquiryRequest,
            collection: RPACollectionResult,
        ) -> InquiryCompletionPayload:
            nonlocal received
            received = collection
            return InquiryCompletionPayload(
                task_result={
                    "success": True,
                    "branchCode": "TEST",
                    "data": {"finalPrice": 100000.0},
                },
                callback_payload={"success": True, "branchCode": "TEST"},
            )

        async def on_terminal(task_id: str, status: TaskStatus) -> None:
            terminal_events.append((task_id, status))

        runtime = RPARuntime(adapters=[adapter], completion_handler=complete)
        runtime.status = ServiceStatus.READY
        runtime.service = RPAInquiryService(
            browsers={adapter.code: None},
            adapters=[adapter],
        )
        runtime.service.sessions[adapter.code] = PlatformSession(
            code=adapter.code,
            name=adapter.name,
            start_url="https://example.test/",
            page=None,
            ready=True,
        )
        runtime.worker_task = asyncio.create_task(runtime._worker_loop())
        try:
            task = await runtime.enqueue_inquiry(
                InquiryRequest(
                    community_name="示例花园一期",
                    area=89.5,
                    city="深圳",
                ),
                terminal_handler=on_terminal,
            )
            await runtime.queue.join()
            assert received is not None
            return received, runtime.get_task(task["taskId"]), terminal_events
        finally:
            runtime.worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime.worker_task

    received, task, terminal_events = asyncio.run(run())

    assert isinstance(received, RPACollectionResult)
    assert task is not None
    assert task["result"]["branchCode"] == "TEST"
    assert terminal_events == [(task["taskId"], TaskStatus.COMPLETED)]
