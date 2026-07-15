# -*- coding: utf-8 -*-
"""服务运行时：浏览器、平台状态、任务队列。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

import nodriver as uc

from app.core import config
from app.core.models import InquiryRequest, InquiryResult, PlatformSession
from app.registry import build_default_adapters
from app.service import RPAInquiryService
from app.utils.task_store import delete_task, save_task, load_pending_tasks
from app.utils.window_control import ensure_browser_foreground, tile_browser_windows

log = logging.getLogger(__name__)

SERVICE_STATUS_TEXT = {
    "BOOTING": "启动中",
    "WAIT_LOGIN": "等待登录",
    "READY": "已就绪",
    "DEGRADED": "部分降级",
    "STOPPING": "已停止",
}

TASK_STATUS_TEXT = {
    "QUEUED": "排队中",
    "RUNNING": "执行中",
    "COMPLETED": "已完成",
    "FAILED": "失败",
}

PLATFORM_STATUS_TEXT = {
    "INIT": "初始化中",
    "WAIT_LOGIN": "等待登录",
    "READY": "已就绪",
    "BUSY": "执行中",
    "WAIT_MANUAL_VERIFY": "等待人工验证",
    "LOGIN_EXPIRED": "登录已失效",
    "NO_DATA": "无数据",
    "SUCCESS": "成功",
    "ERROR": "异常",
}

BRANCH_TEXT = {
    "TAKE_LOWER": "差异在阈值内，取较低值",
    "DEAL_ONLY": "仅采用成交均价",
    "QUOTE_DISCOUNT": "无成交，报价打折",
    "FAILED": "无可用结果",
}


def _to_lower_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camelize(value):
    if isinstance(value, dict):
        return {_to_lower_camel(key): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _camelize_dict(data: dict) -> dict:
    return _camelize(data)


@dataclass(slots=True)
class PlatformRuntimeState:
    code: str
    name: str
    start_url: str
    status: str
    message: str
    last_ready_at: Optional[float] = None
    last_keepalive_at: Optional[float] = None


@dataclass(slots=True)
class InquiryTaskRecord:
    task_id: str
    request: InquiryRequest
    status: str = "QUEUED"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[InquiryResult] = None
    error: Optional[str] = None


async def _default_browser_factory():
    return await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
        browser_args=["--force-device-scale-factor=1"],
    )


class RPARuntime:
    """RPA 服务运行时。"""

    def __init__(
        self,
        *,
        adapters=None,
        browser_factory: Optional[Callable[[], object]] = None,
        keepalive_interval: int = config.PLATFORM_KEEPALIVE_INTERVAL,
        enable_console_ready_confirmation: bool = False,
    ):
        self.adapters = adapters or build_default_adapters()
        self.adapter_map = {adapter.code: adapter for adapter in self.adapters}
        self.browser_factory = browser_factory or _default_browser_factory
        self.keepalive_interval = keepalive_interval
        self.enable_console_ready_confirmation = enable_console_ready_confirmation

        self.browsers: dict[str, object] = {}
        self.browser_pid: Optional[int] = None
        self.service: Optional[RPAInquiryService] = None
        self.platform_states: dict[str, PlatformRuntimeState] = {}
        self.tasks: dict[str, InquiryTaskRecord] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self.keepalive_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.console_confirmation_task: Optional[asyncio.Task] = None
        self.current_task_id: Optional[str] = None
        self.status = "BOOTING"
        self.message = "启动中"

    async def start(self):
        if self.service is not None:
            return

        self.status = "BOOTING"
        self.message = "启动浏览器中"

        # 为每个平台创建独立浏览器实例
        self.browsers = {}
        for adapter in self.adapters:
            browser = await self.browser_factory()
            self.browsers[adapter.code] = browser
            log.info("browser started for %s", adapter.name)

        self.browser_pid = getattr(
            getattr(list(self.browsers.values())[0], "_process", None), "pid", None
        ) if self.browsers else None

        self.service = RPAInquiryService(self.browsers, self.adapters)
        sessions = await self.service.start()

        # 将 5 个浏览器窗口平铺填满屏幕
        browser_pids = [
            getattr(getattr(b, "_process", None), "pid", None)
            for b in self.browsers.values()
        ]
        browser_pids = [p for p in browser_pids if p is not None]
        tile_browser_windows(browser_pids)
        await asyncio.sleep(1.5)  # 等窗口位置生效

        for code, session in sessions.items():
            self.platform_states[code] = PlatformRuntimeState(
                code=code,
                name=session.name,
                start_url=session.start_url,
                status="WAIT_LOGIN",
                message="等待人工登录后确认",
            )

        self.status = "WAIT_LOGIN"
        self.message = "等待人工登录并确认平台就绪"
        self._focus_browser_window("启动完成，等待登录")
        self.worker_task = asyncio.create_task(self._worker_loop(), name="rpa-worker")
        self.keepalive_task = asyncio.create_task(self._keepalive_loop(), name="rpa-keepalive")
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="rpa-heartbeat")
        if self.enable_console_ready_confirmation:
            self.console_confirmation_task = asyncio.create_task(
                self._console_confirmation_loop(),
                name="rpa-console-confirmation",
            )

    async def stop(self):
        for task in (self.worker_task, self.keepalive_task, self.heartbeat_task, self.console_confirmation_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        for browser in getattr(self, "browsers", {}).values():
            try:
                browser.stop()
            except Exception:
                pass

        self.browsers = {}
        self.browser_pid = None
        self.service = None
        self.worker_task = None
        self.keepalive_task = None
        self.heartbeat_task = None
        self.console_confirmation_task = None
        self.current_task_id = None
        self.status = "STOPPING"
        self.message = "已停止"

    def is_ready(self) -> bool:
        return self.status == "READY"

    def snapshot(self) -> dict:
        return {
            "serviceStatusCode": self.status,
            "serviceStatus": SERVICE_STATUS_TEXT.get(self.status, self.status),
            "message": self.message,
            "currentTaskId": self.current_task_id,
            "queueSize": self.queue.qsize(),
            "platforms": [self._serialize_platform_state(item) for item in self.platform_states.values()],
        }

    def get_task(self, task_id: str) -> Optional[dict]:
        record = self.tasks.get(task_id)
        if record is None:
            return None
        return self._serialize_task(record)

    async def enqueue_inquiry(self, request: InquiryRequest) -> dict:
        if not self.is_ready():
            raise RuntimeError("SERVICE_NOT_READY")

        task_id = request.request_id or uuid.uuid4().hex
        request.request_id = task_id
        record = InquiryTaskRecord(task_id=task_id, request=request)
        self.tasks[task_id] = record
        await self.queue.put(task_id)
        # 持久化兜底：入队后写 JSON，进程崩溃后可恢复
        save_task(task_id, asdict(request))
        return self._serialize_task(record)

    async def confirm_platform_ready(self, code: str) -> dict:
        if self.service is None:
            raise RuntimeError("SERVICE_NOT_STARTED")
        if code not in self.adapter_map:
            raise KeyError(code)

        adapter = self.adapter_map[code]
        session: PlatformSession = self.service.sessions[code]
        ready, message = await adapter.check_ready(session)
        state = self.platform_states[code]
        state.message = message
        if ready:
            state.status = "READY"
            state.last_ready_at = time.time()
        else:
            state.status = "WAIT_LOGIN"
        self._refresh_service_status()
        if state.status != "READY":
            self._focus_browser_window(f"{state.name} 仍需人工处理")
        return self._serialize_platform_state(state)

    def _tile_after_login(self):
        """登录确认后重新平铺窗口。"""
        if not hasattr(self, "browsers"):
            return
        pids = []
        for b in self.browsers.values():
            try:
                pid = getattr(getattr(b, "_process", None), "pid", None)
                if pid:
                    pids.append(pid)
            except Exception:
                pass
        if pids:
            tile_browser_windows(pids)

    def _refresh_service_status(self):
        states = list(self.platform_states.values())
        if not states:
            self.status = "BOOTING"
            self.message = "未初始化平台"
            return

        if all(item.status == "READY" for item in states):
            if self.status != "READY":
                self._tile_after_login()
            self.status = "READY"
            self.message = "所有平台已就绪"
            return

        if any(item.status == "WAIT_LOGIN" for item in states):
            self.status = "WAIT_LOGIN"
            self.message = "存在未登录平台"
            return

        if any(item.status == "WAIT_MANUAL_VERIFY" for item in states):
            self.status = "DEGRADED"
            self.message = "存在待人工验证平台"
            return

        self.status = "DEGRADED"
        self.message = "存在异常平台"

    async def _wait_until_ready(self):
        """等待所有平台确认就绪后再执行任务。"""
        while True:
            unready = [
                code for code, state in self.platform_states.items()
                if state.status == "WAIT_LOGIN"
            ]
            if not unready:
                return
            log.info("等待 %d 个平台就绪: %s", len(unready), unready)
            await asyncio.sleep(3)

    async def _worker_loop(self):
        while True:
            task_id = await self.queue.get()
            record = self.tasks[task_id]
            record.status = "RUNNING"
            record.started_at = time.time()
            self.current_task_id = task_id
            try:
                # 等待所有平台就绪
                await self._wait_until_ready()
                # 置前浏览器窗口（nodriver 操作需要焦点）
                self._focus_browser_window("开始执行采集任务")
                assert self.service is not None
                result = await self.service.run_inquiry(record.request)
                record.result = result
                record.status = "COMPLETED"
                self._apply_platform_results(result)
            except Exception as exc:
                log.exception("task failed: %s", task_id)
                record.status = "FAILED"
                record.error = str(exc)
                self.status = "DEGRADED"
                self.message = f"任务执行失败: {exc}"
            finally:
                record.finished_at = time.time()
                self.current_task_id = None
                self.queue.task_done()
                # 任务完成（成功或失败），删除持久化文件
                delete_task(task_id)

    async def _keepalive_loop(self):
        while True:
            await asyncio.sleep(self.keepalive_interval)
            if self.service is None or self.current_task_id is not None:
                continue

            for code, adapter in self.adapter_map.items():
                state = self.platform_states.get(code)
                if state is None or state.status not in {"READY", "WAIT_LOGIN"}:
                    continue

                session = self.service.sessions[code]
                try:
                    ready, message = await adapter.keepalive(session)
                except Exception as exc:
                    ready, message = False, f"保活异常: {exc}"

                state.last_keepalive_at = time.time()
                state.message = message
                if ready:
                    state.status = "READY"
                    state.last_ready_at = time.time()
                elif "验证" in message:
                    state.status = "WAIT_MANUAL_VERIFY"
                else:
                    state.status = "WAIT_LOGIN"

                if state.status in {"WAIT_LOGIN", "WAIT_MANUAL_VERIFY"}:
                    self._focus_browser_window(f"{state.name} 状态变为 {state.status}")

            self._refresh_service_status()

    async def _heartbeat_loop(self):
        """每 HEARTBEAT_INTERVAL 秒发轻量 ping 保持 WebSocket 存活。"""
        while True:
            await asyncio.sleep(config.HEARTBEAT_INTERVAL)
            if self.current_task_id is not None:
                continue  # 采集任务进行中，不打断
            for code, state in self.platform_states.items():
                if state.status not in {"READY", "WAIT_LOGIN"}:
                    continue
                try:
                    session = self.service.sessions.get(code)
                    if session:
                        await session.page.select("body", timeout=3)
                        await session.page
                except Exception:
                    pass

    async def _console_confirmation_loop(self):
        while True:
            pending = [
                state
                for state in self.platform_states.values()
                if state.status in {"WAIT_LOGIN", "WAIT_MANUAL_VERIFY"}
            ]
            if not pending:
                await asyncio.sleep(2)
                continue

            lines = ["", "检测到以下平台尚未就绪："]
            for state in pending:
                lines.append(f"- {state.name}: {state.status} / {state.message}")
            lines.append("请在浏览器完成人工登录或验证后，回到终端按回车继续...")
            prompt = "\n".join(lines)

            try:
                await asyncio.to_thread(input, prompt)
            except EOFError:
                log.warning("控制台不可交互，跳过回车确认流程")
                return

            for state in pending:
                try:
                    result = await self.confirm_platform_ready(state.code)
                    log.info("平台确认结果: %s -> %s", state.name, result["status"])
                except Exception as exc:
                    log.warning("平台确认失败: %s -> %s", state.name, exc)

    def _apply_platform_results(self, result: InquiryResult):
        for item in result.platform_results:
            code = next((key for key, adapter in self.adapter_map.items() if adapter.name == item.name), None)
            if code is None or code not in self.platform_states:
                continue

            state = self.platform_states[code]
            if item.status in {"SUCCESS", "NO_DATA"}:
                state.status = "READY"
                state.message = item.reason or "READY"
                state.last_ready_at = time.time()
            elif item.status == "WAIT_MANUAL_VERIFY":
                state.status = "WAIT_MANUAL_VERIFY"
                state.message = item.reason or "等待人工验证"
                self._focus_browser_window(f"{state.name} 需要人工验证")
            elif item.status == "LOGIN_EXPIRED":
                state.status = "WAIT_LOGIN"
                state.message = item.reason or "登录已失效"
                self._focus_browser_window(f"{state.name} 登录已失效")
            else:
                state.status = "ERROR"
                state.message = item.reason or item.status

        self._refresh_service_status()

    def _serialize_task(self, record: InquiryTaskRecord) -> dict:
        result_payload = None
        if record.result is not None:
            result_payload = {
                "success": record.result.success,
                "finalPrice": record.result.final_price,
                "branchCode": record.result.branch,
                "branch": BRANCH_TEXT.get(record.result.branch, record.result.branch),
                "quoteAvg": record.result.quote_avg,
                "dealAvg": record.result.deal_avg,
                "platform": (
                    self._serialize_platform_result(record.result.platform)
                    if record.result.platform is not None
                    else None
                ),
                "platform_results": [
                    self._serialize_platform_result(item) for item in record.result.platform_results
                ],
                "data": {
                    "quoteAvg": record.result.quote_avg,
                    "dealAvg": record.result.deal_avg,
                    "finalPrice": record.result.final_price,
                },
            }

        return {
            "taskId": record.task_id,
            "statusCode": record.status,
            "status": TASK_STATUS_TEXT.get(record.status, record.status),
            "createdAt": record.created_at,
            "startedAt": record.started_at,
            "finishedAt": record.finished_at,
            "error": record.error,
            "request": _camelize_dict(asdict(record.request)),
            "result": result_payload,
        }

    def _serialize_platform_state(self, state: PlatformRuntimeState) -> dict:
        payload = _camelize_dict(asdict(state))
        payload["statusCode"] = state.status
        payload["status"] = PLATFORM_STATUS_TEXT.get(state.status, state.status)
        return payload

    def _serialize_platform_result(self, item) -> dict:
        payload = _camelize_dict(asdict(item))
        payload["statusCode"] = item.status
        payload["status"] = PLATFORM_STATUS_TEXT.get(item.status, item.status)
        return payload

    def _focus_browser_window(self, reason: str):
        if not self.browser_pid:
            return
        log.info("尝试将浏览器置前: %s", reason)
        ensure_browser_foreground(self.browser_pid)

    def _restore_pending_tasks(self):
        """启动时恢复崩溃前未完成的任务（持久化兜底）。

        正常情况下 persist 目录为空，仅在进程异常退出时残留 JSON 文件。
        """
        pending = load_pending_tasks()
        if not pending:
            return
        for task_data in pending:
            task_id = task_data.pop("task_id")
            request = InquiryRequest(
                community_name=task_data["community_name"],
                area_min=task_data["area_min"],
                area_max=task_data["area_max"],
                city=task_data.get("city", "深圳"),
                request_id=task_id,
            )
            record = InquiryTaskRecord(task_id=task_id, request=request)
            self.tasks[task_id] = record
            self.queue.put_nowait(task_id)
        log.info("restored %d pending task(s) from crash", len(pending))
