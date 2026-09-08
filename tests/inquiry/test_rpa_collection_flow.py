# -*- coding: utf-8 -*-
"""RPA 采集边界测试。"""

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
from app.rpa.platforms.base import _risk_context


class CapturingPlatformAdapter:
    """只记录纯采集输入的最小外壳。"""

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
    async def run() -> tuple[
        RPACollectionResult,
        dict,
        list[tuple[str, TaskStatus]],
        dict,
    ]:
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
                    request_id="client-collection-001",
                ),
                terminal_handler=on_terminal,
            )
            await runtime.queue.join()
            assert received is not None
            record = runtime.tasks[task["taskId"]]
            return (
                received,
                runtime.get_task(task["taskId"]),
                terminal_events,
                runtime._build_callback_payload(record),
            )
        finally:
            runtime.worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime.worker_task

    received, task, terminal_events, callback = asyncio.run(run())

    assert isinstance(received, RPACollectionResult)
    assert task is not None
    assert task["taskId"] != "client-collection-001"
    assert task["requestId"] == "client-collection-001"
    assert callback["taskId"] == task["taskId"]
    assert callback["requestId"] == "client-collection-001"
    assert task["result"]["branchCode"] == "TEST"
    assert terminal_events == [(task["taskId"], TaskStatus.COMPLETED)]


def test_runtime_start_rolls_back_browsers_and_can_retry() -> None:
    class Browser:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    class StartAdapter:
        code = "start-test"
        name = "启动测试平台"
        uses_browser = True

        def __init__(self) -> None:
            self.open_attempts = 0

        async def open_session(self, browser, new_tab=False) -> PlatformSession:
            self.open_attempts += 1
            if self.open_attempts == 1:
                raise RuntimeError("模拟平台启动失败")
            return PlatformSession(
                code=self.code,
                name=self.name,
                start_url="https://example.test/",
                page=None,
                ready=True,
            )

        async def collect(self, browser, session, request):
            raise AssertionError("启动测试不应进入采集")

        def detect_block(self, url, html):
            return False, ""

    async def run() -> tuple[list[Browser], RPARuntime]:
        browsers: list[Browser] = []
        adapter = StartAdapter()

        async def factory() -> Browser:
            browser = Browser()
            browsers.append(browser)
            return browser

        runtime = RPARuntime(adapters=[adapter], browser_factory=factory)
        try:
            await runtime.start()
        except RuntimeError as exc:
            assert str(exc) == "模拟平台启动失败"
        else:
            raise AssertionError("第一次启动应失败")

        assert runtime.service is None
        assert runtime.browsers == {}
        assert runtime.platform_states == {}
        assert runtime.status == ServiceStatus.DEGRADED
        assert [browser.stopped for browser in browsers] == [True]

        await runtime.start()
        assert runtime.service is not None
        assert runtime.status == ServiceStatus.WAIT_LOGIN
        await runtime.stop()
        return browsers, runtime

    browsers, runtime = asyncio.run(run())
    assert [browser.stopped for browser in browsers] == [True, True]
    assert runtime.service is None


def test_risk_context_uses_explicit_platform_code_for_module_function() -> None:
    def module_detect_block(url, html):
        return False, ""

    module_detect_block.__module__ = "app.rpa.platforms.ke.collector"

    assert _risk_context(module_detect_block, "挂牌页", "ke") == "贝壳(ke)/挂牌页"
