# -*- coding: utf-8 -*-
"""行舟深房（xzsfbj）采集 MVP 脚本。

复用 rpa 现有组件（print_mvp_result / prepare_listing_data_with_reference /
short_circuit_result），结构对齐 ke_mvp_test.py。

链路：WMPF CDP 捕获 token → xqData.json 匹配 regionId → AES 加密 → httpx 调接口
→ prepare_listing_data 过滤 → print_mvp_result 输出。

用法：
    powershell -ExecutionPolicy Bypass -File scripts/setup_xzsfbj_wmpf_bridge.ps1
    python app/scripts/xzsfbj_mvp_test.py --community 月亮湾花园 --area 91.5 --debug
    python app/scripts/xzsfbj_mvp_test.py --community 月亮湾花园 --area 91.5
    python app/scripts/xzsfbj_mvp_test.py --community 月亮湾花园,大冲城市花园
    python app/scripts/xzsfbj_mvp_test.py --scroll-mvp
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import websockets

# 复用 rpa 的日志配置、状态、模型、公共函数、MVP 输出
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.utils.logging_utils import setup_logging
from app.core import config
from app.core.algorithm import AlgorithmInput, evaluate_algorithm
from app.core.status import (
    PlatformResultStatus,
    PLATFORM_RESULT_STATUS_TEXT,
)
from app.core.models import PlatformResult, ListingSnapshot
from app.core.price_utils import round_price
from app.platforms.base import (
    listing_no_data_reason,
    listing_no_data_status,
    prepare_listing_data_with_reference,
    short_circuit_result,
)
from app.platforms import xzsfbj_constants as constants
from app.platforms.adapters.xzsfbj import DealPageRisk
from app.parsers.xzsfbj import (
    filter_deal_records,
    find_community_candidates,
    parse_community_index,
    parse_listing_snapshots,
)
from app.utils.debug_utils import dump_html as shared_dump_html
from app.utils.debug_utils import is_debug_mode, set_debug_mode
from app.utils.mvp_result import print_mvp_result

setup_logging()
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app.platforms.adapters.xzsfbj")

# ---- 常量（均已实测验证）----
BASE_URL = "https://www.xzsfbj.com.cn"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF"
)
REFERER = "https://servicewechat.com/wxd49effb77288061d/56/page-frame.html"

DEBUG_DIR = config.DEBUG_DIR
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WMPF_BRIDGE_DIR_ENV = "XZSFBJ_WMPF_BRIDGE_DIR"
DEFAULT_WMPF_BRIDGE_DIR = PROJECT_ROOT / "third_party" / "zhong_wmpf_bridge"
TOKEN_CAPTURE_TIMEOUT = 30
XQ_DATA_PATH_ENV = "XZSFBJ_XQ_DATA_PATH"
MINI_PROGRAM_APP_ID = "wxd49effb77288061d"
CDP_PROXY_URL = "ws://127.0.0.1:62000"
UI_INPUT_COMMAND_TIMEOUT = 5.0

# UI MVP 使用 CDP Input，坐标是小程序渲染视口坐标，不是 Windows 桌面坐标。
# 这些值只是当前微信窗口布局的默认值，实际窗口改变时通过命令行覆盖。
DEFAULT_SEARCH_POINT = (200.0, 228.0)
# 搜索结果页第一张小区卡片的两种已验证布局：
# 无顶部统计提示条时卡片较高；有提示条时卡片整体下移。
DEFAULT_RESULT_POINT = (205.0, 260.0)
DEFAULT_RESULT_POINT_WITH_NOTICE = (205.0, 350.0)
DEFAULT_ONSALE_POINT = (205.0, 457.0)
DEFAULT_AREA_FILTER_POINT = (126.0, 138.0)
DEFAULT_BACK_POINT = (22.0, 42.0)
DEFAULT_SCROLL_X = 200.0
DEFAULT_SCROLL_CENTER_Y = 470.0
DEFAULT_SCROLL_DELTA = 520.0

_AREA_RANGE_PATTERN = re.compile(
    r"(?P<minimum>\d+(?:\.\d+)?)\s*[-~～至到－–—]\s*"
    r"(?P<maximum>\d+(?:\.\d+)?)\s*"
    r"(?:㎡|m²|m2|平方米|平米|平方|平)",
    re.IGNORECASE,
)
_AREA_UPPER_PATTERN = re.compile(
    r"(?P<minimum>\d+(?:\.\d+)?)\s*"
    r"(?:㎡|m²|m2|平方米|平米|平方|平)\s*"
    r"(?:以上|及以上|\+)",
    re.IGNORECASE,
)
_AREA_LOWER_PATTERN = re.compile(
    r"(?P<maximum>\d+(?:\.\d+)?)\s*"
    r"(?:㎡|m²|m2|平方米|平米|平方|平)\s*"
    r"(?:以下|及以下|以内)",
    re.IGNORECASE,
)

# 防风控参数：行舟深房接口请求间隔固定为 11 秒
PAGE_GAP_MIN = 11.0
PAGE_GAP_MAX = 11.0
STEP_GAP_MIN = 11.0
STEP_GAP_MAX = 11.0
DEAL_AREA_TOLERANCE = constants.DEAL_AREA_TOLERANCE
DEAL_LOOKBACK_MONTHS = constants.DEAL_LOOKBACK_MONTHS

# token 主动刷新策略（双触发）
MAX_COMMUNITIES_PER_TOKEN = 30
REFRESH_INTERVAL = 3600


# ============================================================
# token 获取（行舟深房特有：WMPF CDP 网络事件）
# ============================================================

def _resolve_wmpf_bridge_dir() -> Path:
    """返回环境变量指定或项目内置的 WMPF 调试桥路径。"""
    configured = os.environ.get(WMPF_BRIDGE_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_WMPF_BRIDGE_DIR


def _resolve_xq_data_file() -> Path:
    """查找当前微信用户的小程序本地小区清单，支持显式覆盖。"""
    configured = os.environ.get(XQ_DATA_PATH_ENV) or os.environ.get("XQ_DATA_PATH")
    if configured:
        return Path(configured).expanduser().resolve()

    appdata = os.environ.get("APPDATA")
    users_dir = Path(appdata or ".") / "Tencent" / "xwechat" / "radium" / "users"
    candidates = list(users_dir.glob(
        f"*/applet/local/{MINI_PROGRAM_APP_ID}/usr/xqData.json"
    ))
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    return users_dir / "<current-user>" / "applet" / "local" / MINI_PROGRAM_APP_ID / "usr" / "xqData.json"


XQ_DATA_FILE = _resolve_xq_data_file()


def _build_wmpf_bridge_command() -> tuple[list[str], Path]:
    """构造本机 WMPF 调试桥启动命令。"""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("未找到 Node.js，无法启动 WMPF 调试桥")
    bridge_dir = _resolve_wmpf_bridge_dir()
    if not (bridge_dir / "src" / "index.js").exists():
        raise RuntimeError(f"未找到 WMPF 调试桥: {bridge_dir}")
    if not (bridge_dir / "node_modules" / "frida").exists():
        raise RuntimeError(
            "WMPF 调试桥依赖未安装，请先运行 "
            "powershell -ExecutionPolicy Bypass -File scripts/setup_xzsfbj_wmpf_bridge.ps1"
        )
    return [
        node,
        "src/index.js",
    ], bridge_dir


async def _wmpf_bridge_is_reachable() -> bool:
    """检查默认 CDP 端口是否仍被旧的 WMPF WebSocket 桥占用。"""
    try:
        async with asyncio.timeout(1.5):
            async with websockets.connect(
                CDP_PROXY_URL,
                open_timeout=1.0,
                close_timeout=0.2,
            ):
                return True
    except (OSError, TimeoutError, websockets.WebSocketException):
        return False


async def _stop_stale_wmpf_bridge() -> list[int]:
    """清理占用 CDP 端口且命令行明确属于本项目的残留桥进程。"""
    if os.name != "nt":
        return []

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return []
    command = r"""
$owners = @(Get-NetTCPConnection -LocalPort 62000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($id in $owners) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
    if ($null -eq $process) { continue }
    $commandLine = [string]$process.CommandLine
    if ($commandLine -match '(?i)(zhong_wmpf_bridge|src[\\/]+index\.js)') {
        Stop-Process -Id ([int]$id) -Force -ErrorAction Stop
        Write-Output ([int]$id)
    }
}
"""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        log.warning("检查残留 WMPF 调试桥失败: %r", exc)
        return []
    if result.returncode != 0:
        log.warning("清理残留 WMPF 调试桥失败: %s", result.stderr.strip())
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


async def _start_wmpf_bridge():
    """启动并确认 WMPF 调试桥已完成 Frida 注入。"""
    log.info("启动 WMPF 调试桥（不修改系统代理）...")
    # 每次批量/单条启动都建立全新的桥接会话，避免旧桥保留已经断开的
    # miniapp runtime 或 DevTools client，导致新会话收到 1012 service restart。
    stale_pids = await _stop_stale_wmpf_bridge()
    if stale_pids:
        log.info("启动前已清理残留 WMPF 调试桥 pid=%s", stale_pids)
    for _ in range(10):
        if not await _wmpf_bridge_is_reachable():
            break
        await asyncio.sleep(0.2)
    else:
        raise RuntimeError(
            "启动前清理后仍有进程占用 62000 调试桥端口；"
            "请关闭残留 Node/WMPF 调试桥后重试"
        )
    command, bridge_dir = _build_wmpf_bridge_command()
    proc = subprocess.Popen(
        command,
        cwd=bridge_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if proc.stdout is None:
        raise RuntimeError("WMPF 调试桥未提供启动输出")

    deadline = asyncio.get_running_loop().time() + 15
    lines: list[str] = []
    while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
        try:
            line = await asyncio.wait_for(
                asyncio.to_thread(proc.stdout.readline), timeout=remaining,
            )
        except TimeoutError:
            break
        if not line:
            break
        message = line.strip()
        lines.append(message)
        if "[frida] script loaded" in message:
            log.info("WMPF 调试桥已就绪")
            return proc

    await _stop_wmpf_bridge(proc)
    detail = "\n".join(lines[-40:]) or f"退出码={proc.returncode}"
    raise RuntimeError(f"WMPF 调试桥启动失败，完整输出如下:\n{detail}")


async def _stop_wmpf_bridge(proc) -> None:
    if proc is None:
        return
    pid = getattr(proc, "pid", "-")
    if proc.poll() is not None:
        log.info("WMPF 调试桥已退出 pid=%s code=%s", pid, proc.returncode)
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        log.info("WMPF 调试桥已退出 pid=%s", pid)
        return
    try:
        await asyncio.to_thread(proc.wait, 5)
    except subprocess.TimeoutExpired:
        proc.kill()
        await asyncio.to_thread(proc.wait, 5)
    log.info("WMPF 调试桥已关闭 pid=%s code=%s", pid, proc.returncode)


class _CdpClient:
    """复用一个 CDP websocket，同时分发响应和网络事件。

    重要：命令默认不设超时。WMPF 桥在小程序尚未连接时会丢弃命令，
    这时用短超时循环重发只会制造悬挂请求和错误日志。UI MVP 在发送命令前
    先让人工确认小程序已经打开，之后由 websocket 断开来结束等待。
    """

    def __init__(self, socket) -> None:
        self.socket = socket
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw_message in self.socket:
                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    continue
                message_id = message.get("id")
                future = self._pending.get(message_id)
                if future is not None and not future.done():
                    future.set_result(message)
                elif message.get("method"):
                    await self.events.put(message)
        except (OSError, asyncio.CancelledError, websockets.WebSocketException):
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_result(None)

    async def command(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict | None:
        message_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self.socket.send(json.dumps({
                "id": message_id,
                "method": method,
                "params": params or {},
            }))
            command_timeout = timeout
            if command_timeout is None and method.startswith("Input."):
                # WMPF 在页面切换期间可能不回 Input 命令；输入事件不能无限
                # 阻塞整批任务，但页面/Network 等待仍保持原有无固定超时语义。
                command_timeout = UI_INPUT_COMMAND_TIMEOUT
            try:
                if command_timeout is None:
                    return await future
                return await asyncio.wait_for(future, timeout=command_timeout)
            except TimeoutError:
                log.warning(
                    "CDP 命令等待超时: method=%s id=%s timeout=%.1fs",
                    method,
                    message_id,
                    command_timeout,
                )
                return None
        finally:
            self._pending.pop(message_id, None)

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)


class _CdpDomPage:
    """让现有 dump_html 辅助复用 CDP Runtime 的页面内容。"""

    def __init__(self, client: _CdpClient) -> None:
        self.client = client

    async def get_content(self) -> str:
        response = await self.client.command(
            "Runtime.evaluate",
            {
                "expression": (
                    "String(document.documentElement && "
                    "document.documentElement.outerHTML || String())"
                ),
                "returnByValue": True,
            },
            timeout=5.0,
        )
        if response is None or "error" in response:
            raise RuntimeError("Runtime.evaluate outerHTML 失败")
        value = response.get("result", {}).get("result", {}).get("value")
        if not isinstance(value, str):
            raise RuntimeError("Runtime.evaluate 未返回 outerHTML")
        return value


async def _dump_ui_dom(
    client: _CdpClient,
    name: str,
    terms: tuple[str, ...] = (),
) -> None:
    """在 --debug 下保存 CDP DOM 和可见文本节点的坐标快照。"""
    if not is_debug_mode():
        return

    try:
        html_path = await shared_dump_html(
            _CdpDomPage(client),
            f"xzsfbj_ui_{name}",
            logger=log,
        )
        encoded_terms = json.dumps(terms, ensure_ascii=False)
        response = await client.command(
            "Runtime.evaluate",
            {
                "expression": (
                    "(() => {"
                    f"const terms = {encoded_terms};"
                    "const html = String(document.documentElement && "
                    "document.documentElement.outerHTML || String());"
                    "const body = String(document.body && document.body.innerText || String());"
                    "const matches = Array.from(document.querySelectorAll('*'))"
                    ".filter(node => {"
                    "const text = String(node.textContent || '').replace(/\\s+/g, ' ').trim();"
                    "return terms.some(term => term && text.includes(term));"
                    "})"
                    ".slice(0, 100)"
                    ".map(node => {"
                    "const rect = node.getBoundingClientRect();"
                    "const style = getComputedStyle(node);"
                    "return {"
                    "tag: node.tagName, id: node.id || '', "
                    "className: String(node.className || ''), "
                    "text: String(node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 240), "
                    "x: rect.x, y: rect.y, width: rect.width, height: rect.height, "
                    "display: style.display, visibility: style.visibility, "
                    "visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'"
                    "};"
                    "});"
                    "return {"
                    "url: String(location.href), title: String(document.title), "
                    "readyState: String(document.readyState), bodyText: body.slice(0, 4000), "
                    "bodyTextLength: body.length, htmlLength: html.length, "
                    "htmlContains: terms.filter(term => term && html.includes(term)), "
                    "nodeCount: document.querySelectorAll('*').length, matches"
                    "};"
                    "})()"
                ),
                "returnByValue": True,
            },
            timeout=5.0,
        )
        snapshot = response.get("result", {}).get("result", {}).get("value") if response else None
        if not isinstance(snapshot, dict):
            snapshot = {"error": "Runtime.evaluate DOM 快照失败", "response": response}
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_path = DEBUG_DIR / f"xzsfbj_ui_{name}.json"
        snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(
            "UI DOM 已导出: html=%s snapshot=%s body_chars=%s matches=%s",
            html_path,
            snapshot_path,
            snapshot.get("bodyTextLength", "-"),
            len(snapshot.get("matches", [])) if isinstance(snapshot.get("matches"), list) else "-",
        )
    except Exception as exc:
        log.warning("UI DOM 导出失败 name=%s: %r", name, exc)


def _request_url_from_headers(headers: dict) -> str:
    """从 ExtraInfo 伪首部拼出请求 URL，不读取具体请求头内容。"""
    scheme = headers.get(":scheme")
    authority = headers.get(":authority")
    path = headers.get(":path")
    if scheme and authority and path:
        return f"{scheme}://{authority}{path}"
    return ""


def _url_path(url: str) -> str:
    """Return only the URL path; query values are intentionally discarded."""
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return ""


def _is_xzsfbj_url(url: str) -> bool:
    """判断 URL 是否属于行舟深房，不依赖请求路径是否已被合成。"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "xzsfbj.com.cn" or host.endswith(".xzsfbj.com.cn")


def _normalize_cdp_headers(headers: object) -> dict:
    """兼容 WMPF 返回的对象式和列表式 headers。"""
    if isinstance(headers, dict):
        return headers
    if isinstance(headers, list):
        normalized: dict[str, object] = {}
        for item in headers:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name:
                normalized[str(name)] = item.get("value", "")
        return normalized
    return {}


def _infer_api_path(url: str, payload: object) -> str:
    """URL 缺失时从已核对过的响应结构识别在售/成交接口。"""
    path = _url_path(url)
    if constants.API_SALES_PATH in path:
        return constants.API_SALES_PATH
    if constants.API_DEALS_PATH in path:
        return constants.API_DEALS_PATH
    if not isinstance(payload, dict):
        return ""

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("dealList"), list):
        return constants.API_DEALS_PATH
    if not isinstance(data, list):
        return ""

    # getCommunitySales 的每条记录同时包含面积、单价和挂牌总价；
    # 这个结构与小区详情页的其它 JSON 列表不同。
    if any(
        isinstance(item, dict)
        and {"acreage", "unitPrice", "price"}.issubset(item)
        for item in data
    ):
        return constants.API_SALES_PATH
    return ""


@dataclass(slots=True)
class _CapturedResponse:
    """Network 响应的脱离 CDP 后数据。"""

    request_id: str
    url: str
    status: int | None
    payload: object
    path: str


class _NetworkMonitor:
    """后台消费 CDP Network 事件，并按响应体保存业务 JSON。

    UI 控制只负责发送输入事件；所有请求和响应均由这个后台任务顺序处理，
    因此滚动时不会因为主流程在等待 UI 而漏掉网络事件。
    """

    def __init__(self, client: _CdpClient, stop_event: asyncio.Event) -> None:
        self.client = client
        self.stop_event = stop_event
        self.event_counts: dict[str, int] = {}
        self.responses: list[_CapturedResponse] = []
        self.request_urls: dict[str, str] = {}
        self.response_urls: dict[str, str] = {}
        self.response_statuses: dict[str, int] = {}
        self.xzsfbj_request_ids: set[str] = set()
        self.activity_count = 0
        self.activity_event = asyncio.Event()
        self.response_event = asyncio.Event()

    def response_count(self, path: str) -> int:
        return sum(item.path == path for item in self.responses)

    def unique_sales_count(self) -> int:
        """统计当前已捕获在售响应中的去重房源数。"""
        house_ids: set[str] = set()
        for response in self.responses:
            if response.path != constants.API_SALES_PATH or not isinstance(response.payload, dict):
                continue
            data = response.payload.get("data")
            if not isinstance(data, list):
                continue
            for index, item in enumerate(data):
                if not isinstance(item, dict):
                    continue
                house_id = str(item.get("id") or item.get("houseId") or "")
                house_ids.add(house_id or f"{response.request_id}:{index}")
        return len(house_ids)

    def reset_capture(self) -> None:
        """开始下一条 UI 记录时清空上一条的 Network 捕获状态。"""
        self.event_counts.clear()
        self.responses.clear()
        self.request_urls.clear()
        self.response_urls.clear()
        self.response_statuses.clear()
        self.xzsfbj_request_ids.clear()
        self.activity_count = 0
        self.activity_event.clear()
        self.response_event.clear()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                event = await asyncio.wait_for(self.client.events.get(), timeout=1.0)
            except TimeoutError:
                continue
            await self._consume(event)

    async def _consume(self, event: dict) -> None:
        method = str(event.get("method") or "")
        self.event_counts[method] = self.event_counts.get(method, 0) + 1
        self.activity_count += 1
        self.activity_event.set()
        params = event.get("params", {})
        request_id = str(params.get("requestId", ""))

        if method == "Network.requestWillBeSent":
            url = str(params.get("request", {}).get("url", ""))
            if request_id and url:
                self.request_urls[request_id] = url
                if _is_xzsfbj_url(url) and (
                    constants.API_SALES_PATH in _url_path(url)
                    or constants.API_DEALS_PATH in _url_path(url)
                ):
                    self.xzsfbj_request_ids.add(request_id)
            self._log_path("请求", url)
            return

        if method == "Network.requestWillBeSentExtraInfo":
            headers = _normalize_cdp_headers(params.get("headers", {}))
            url = _request_url_from_headers(headers)
            if request_id and url:
                self.request_urls[request_id] = url
            if request_id and (_is_xzsfbj_request(headers) or _is_xzsfbj_url(url)):
                self.xzsfbj_request_ids.add(request_id)
            self._log_path("请求 ExtraInfo", url)
            return

        if method == "Network.responseReceived":
            response = params.get("response", {})
            url = str(response.get("url", ""))
            if request_id and url:
                self.response_urls[request_id] = url
                if _is_xzsfbj_url(url):
                    self.xzsfbj_request_ids.add(request_id)
            status = response.get("status")
            if request_id and isinstance(status, int):
                self.response_statuses[request_id] = status
            self._log_path("响应", url)
            return

        if method == "Network.responseReceivedExtraInfo":
            status = params.get("statusCode")
            if request_id and isinstance(status, int):
                self.response_statuses[request_id] = status
            return

        if method == "Network.loadingFailed":
            self.request_urls.pop(request_id, None)
            self.response_urls.pop(request_id, None)
            self.response_statuses.pop(request_id, None)
            self.xzsfbj_request_ids.discard(request_id)
            return

        if method != "Network.loadingFinished":
            return

        url = (
            self.response_urls.pop(request_id, "")
            or self.request_urls.pop(request_id, "")
        )
        candidate = request_id in self.xzsfbj_request_ids
        self.xzsfbj_request_ids.discard(request_id)
        path = _url_path(url)
        if not candidate and not (
            constants.API_SALES_PATH in path or constants.API_DEALS_PATH in path
        ):
            self.response_statuses.pop(request_id, None)
            return

        body_response = await self._get_response_body(request_id)
        payload = self._decode_json_body(body_response, request_id)
        if payload is None:
            self.response_statuses.pop(request_id, None)
            return
        path = _infer_api_path(url, payload)
        if not path:
            self.response_statuses.pop(request_id, None)
            return
        status = self.response_statuses.pop(request_id, None)
        if status is None and isinstance(params.get("status"), int):
            status = params["status"]
        captured = _CapturedResponse(request_id, url, status, payload, path)
        self.responses.append(captured)
        self.response_event.set()
        data = payload.get("data") if isinstance(payload, dict) else None
        count = len(data) if isinstance(data, list) else None
        log.info(
            "Network 已保存响应 path=%s url=%s data=%s cumulative_sales=%d",
            path,
            url or "<WMPF未提供URL>",
            count if count is not None else "-",
            self.unique_sales_count(),
        )
        if is_debug_mode():
            _dump_api_payload(
                f"ui_{'sales' if path == constants.API_SALES_PATH else 'deals'}_"
                f"{self.response_count(path)}",
                payload,
            )

    async def _get_response_body(self, request_id: str) -> dict | None:
        # loadingFinished 后极短时间内 body 仍可能尚未挂到调试会话，
        # 只对这个瞬时 CDP 状态做重试，不给 UI 流程设置超时。
        for delay in (0.0, 0.2, 0.6):
            if delay:
                await asyncio.sleep(delay)
            response = await self.client.command(
                "Network.getResponseBody",
                {"requestId": request_id},
                timeout=15.0,
            )
            if response is not None and "error" not in response:
                return response
            if response and response.get("error", {}).get("code") not in {-32000, -32001}:
                break
        log.warning("Network 响应体不可读 request_id=%s", request_id)
        return None

    @staticmethod
    def _decode_json_body(response: dict | None, request_id: str) -> object | None:
        body = (response or {}).get("result", {}).get("body")
        if not isinstance(body, str):
            log.warning("Network 响应体为空 request_id=%s", request_id)
            return None
        if (response or {}).get("result", {}).get("base64Encoded"):
            try:
                body = base64.b64decode(body).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                log.warning("Network 响应体不是 UTF-8 request_id=%s", request_id)
                return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            log.warning("Network 响应体不是 JSON request_id=%s", request_id)
            return None

    @staticmethod
    def _log_path(label: str, url: str) -> None:
        path = _url_path(url)
        if path.startswith("/api/"):
            log.info("Network %s path=%s", label, path)

    async def wait_for_activity(self, previous: int) -> None:
        """等待一次新的 Network 活动，不限制人工操作耗时。"""
        while self.activity_count <= previous:
            self.activity_event.clear()
            await self.activity_event.wait()

    async def wait_for_response(self, path: str, previous: int) -> _CapturedResponse:
        """等待指定接口的新响应，不以固定秒数判断页面是否完成。"""
        last_report_at = asyncio.get_running_loop().time()
        while True:
            matches = [response for response in self.responses if response.path == path]
            if len(matches) > previous:
                return matches[previous]
            self.response_event.clear()
            try:
                # 这里只是定期输出进度，不是业务超时；等待仍然持续到真实响应或用户中断。
                await asyncio.wait_for(self.response_event.wait(), timeout=8.0)
            except TimeoutError:
                now = asyncio.get_running_loop().time()
                if now - last_report_at >= 8.0:
                    log.info(
                        "仍等待 Network 响应 path=%s；事件=%s；已保存响应=%d",
                        path,
                        self.event_counts,
                        len(self.responses),
                    )
                    last_report_at = now


async def _probe_page_context(client: _CdpClient) -> dict[str, object]:
    """Probe Runtime/DOM without printing page text or DOM contents."""
    result: dict[str, object] = {
        "runtime_enabled": False,
        "dom_available": False,
        "text_readable": False,
        "text_length": 0,
    }
    runtime_response = await client.command("Runtime.enable")
    result["runtime_enabled"] = (
        runtime_response is not None and "error" not in runtime_response
    )
    dom_response = await client.command(
        "DOM.getDocument",
        {"depth": 1, "pierce": True},
    )
    result["dom_available"] = bool(
        dom_response and isinstance(dom_response.get("result", {}).get("root"), dict)
    )
    evaluate_response = await client.command(
        "Runtime.evaluate",
        {
            "expression": "String(document.body && document.body.innerText || '')",
            "returnByValue": True,
        },
    )
    value = (
        (evaluate_response or {})
        .get("result", {})
        .get("result", {})
        .get("value")
    )
    if isinstance(value, str):
        result["text_readable"] = True
        result["text_length"] = len(value)
    return result


async def _tap(client: _CdpClient, x: float, y: float) -> bool:
    """按真人节奏发送一次带轻微落点偏移的触摸点击。"""
    x += random.uniform(-3.0, 3.0)
    y += random.uniform(-3.0, 3.0)
    start = await client.command(
        "Input.dispatchTouchEvent",
        {
            "type": "touchStart",
            "touchPoints": [{"id": 1, "x": x, "y": y, "force": 0.8}],
            "modifiers": 0,
        },
    )
    await asyncio.sleep(random.uniform(0.09, 0.18))
    move = await client.command(
        "Input.dispatchTouchEvent",
        {
            "type": "touchMove",
            "touchPoints": [{"id": 1, "x": x + random.uniform(-1.5, 1.5), "y": y + random.uniform(-1.5, 1.5), "force": 0.65}],
            "modifiers": 0,
        },
    )
    await asyncio.sleep(random.uniform(0.04, 0.10))
    end = await client.command(
        "Input.dispatchTouchEvent",
        {"type": "touchEnd", "touchPoints": [], "modifiers": 0},
    )
    await asyncio.sleep(random.uniform(0.25, 0.55))
    return all(item is not None and "error" not in item for item in (start, move, end))


async def _mouse_tap(client: _CdpClient, x: float, y: float) -> bool:
    """按桌面微信的鼠标输入模型点击，行为更接近浏览器自动化。"""
    x += random.uniform(-3.0, 3.0)
    y += random.uniform(-3.0, 3.0)
    move = await client.command(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseMoved",
            "x": x,
            "y": y,
            "button": "none",
            "buttons": 0,
            "pointerType": "mouse",
        },
    )
    await asyncio.sleep(random.uniform(0.08, 0.16))
    press = await client.command(
        "Input.dispatchMouseEvent",
        {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 1,
            "clickCount": 1,
            "pointerType": "mouse",
        },
    )
    await asyncio.sleep(random.uniform(0.09, 0.18))
    release = await client.command(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 0,
            "clickCount": 1,
            "pointerType": "mouse",
        },
    )
    await asyncio.sleep(random.uniform(0.35, 0.75))
    return all(
        item is not None and "error" not in item
        for item in (move, press, release)
    )


async def _ui_tap(
    client: _CdpClient,
    x: float,
    y: float,
    input_mode: str,
) -> bool:
    """按指定输入模型点击小程序控件。"""
    if input_mode == "touch":
        return await _tap(client, x, y)
    return await _mouse_tap(client, x, y)


async def _read_ui_community_candidates(
    client: _CdpClient,
    community: str,
) -> list[dict[str, object]]:
    """从 WMPF 主文档和 iframe 中读取目标小区标题坐标。"""
    encoded_community = json.dumps(
        re.sub(r"\s+", "", community),
        ensure_ascii=False,
    )
    expression = (
        "(() => {"
        "const target = " + encoded_community + ";"
        "const documents = [];"
        "const visited = new Set();"
        "const viewportWidth = Number(window.innerWidth || document.documentElement.clientWidth || 0);"
        "const viewportHeight = Number(window.innerHeight || document.documentElement.clientHeight || 0);"
        "const intersectsViewport = rect => rect.right > 0 && rect.bottom > 0 &&"
        "  rect.left < viewportWidth && rect.top < viewportHeight;"
        "const collectDocument = (doc, offsetX, offsetY, framePath, topFrameElement) => {"
        "  if (!doc || visited.has(doc)) return;"
        "  visited.add(doc); documents.push({doc, offsetX, offsetY, framePath, topFrameElement});"
        "  for (const [index, frame] of Array.from(doc.querySelectorAll('iframe')).entries()) {"
        "    let child = null; try { child = frame.contentDocument; } catch (_) {}"
        "    if (!child) continue;"
        "    const box = frame.getBoundingClientRect();"
        "    const view = doc.defaultView || window;"
        "    const style = view.getComputedStyle(frame);"
        "    if (!intersectsViewport(box) || style.display === 'none' ||"
        "        style.visibility === 'hidden') continue;"
        "    collectDocument(child, offsetX + box.left, offsetY + box.top, "
        "      framePath + '.iframe[' + index + ']', topFrameElement || frame);"
        "  }"
        "};"
        "const isActiveFrame = (item, x, y) => {"
        "  if (!item.topFrameElement) return true;"
        "  return document.elementFromPoint(x, y) === item.topFrameElement;"
        "};"
        "collectDocument(document, 0, 0, 'top', null);"
        "const candidates = [];"
        "const onsaleNodes = [];"
        "for (const item of documents) {"
        "  const view = item.doc.defaultView || window;"
        "  for (const node of Array.from(item.doc.querySelectorAll('*'))) {"
        "    const nodeText = String(node.textContent || '').replace(/\\s+/g, ' ').trim();"
        "    if (nodeText.replace(/\\s+/g, '') !== '当前在售房源') continue;"
        "    const nodeRect = node.getBoundingClientRect();"
        "    const nodeStyle = view.getComputedStyle(node);"
        "    if (nodeRect.width <= 0 || nodeRect.height <= 0 ||"
        "        nodeStyle.display === 'none' || nodeStyle.visibility === 'hidden') continue;"
        "    const nodeX = nodeRect.left + item.offsetX;"
        "    const nodeY = nodeRect.top + item.offsetY;"
        "    if (!intersectsViewport({left: nodeX, top: nodeY,"
        "        right: nodeX + nodeRect.width, bottom: nodeY + nodeRect.height}) ||"
        "        !isActiveFrame(item, nodeX + nodeRect.width / 2, nodeY + nodeRect.height / 2)) continue;"
        "    onsaleNodes.push({"
        "      x: nodeX, y: nodeY,"
        "      width: nodeRect.width, height: nodeRect.height,"
        "      tag: String(node.tagName || ''), className: String(node.className || ''),"
        "      framePath: item.framePath"
        "    });"
        "  }"
        "}"
        "for (const item of documents) {"
        "  const view = item.doc.defaultView || window;"
        "  for (const node of Array.from(item.doc.querySelectorAll('*'))) {"
        "    const text = String(node.textContent || '').replace(/\\s+/g, ' ').trim();"
        "    const normalized = text.replace(/\\s+/g, '');"
        "    if (!normalized || !normalized.includes(target) || text.length > 160) continue;"
        "    const rect = node.getBoundingClientRect();"
        "    const style = view.getComputedStyle(node);"
        "    if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || "
        "        style.visibility === 'hidden') continue;"
        "    const nodeX = rect.left + item.offsetX;"
        "    const nodeY = rect.top + item.offsetY;"
        "    if (!intersectsViewport({left: nodeX, top: nodeY,"
        "        right: nodeX + rect.width, bottom: nodeY + rect.height}) ||"
        "        !isActiveFrame(item, nodeX + rect.width / 2, nodeY + rect.height / 2)) continue;"
        "    let onsale = null;"
        "    for (const child of Array.from(node.querySelectorAll('*'))) {"
        "      const childText = String(child.textContent || '').replace(/\\s+/g, ' ').trim();"
        "      if (childText.replace(/\\s+/g, '') !== '当前在售房源') continue;"
        "      const childRect = child.getBoundingClientRect();"
        "      const childStyle = view.getComputedStyle(child);"
        "      if (childRect.width > 0 && childRect.height > 0 && "
        "          childStyle.display !== 'none' && childStyle.visibility !== 'hidden') {"
        "        onsale = {"
        "          x: childRect.left + item.offsetX, y: childRect.top + item.offsetY,"
        "          width: childRect.width, height: childRect.height,"
        "          tag: String(child.tagName || ''), className: String(child.className || '')"
        "        };"
        "        break;"
        "      }"
        "    }"
        "    if (!onsale) {"
        "      const right = nodeX + rect.width;"
        "      const bottom = nodeY + rect.height;"
        "      const contained = onsaleNodes.filter(candidate => {"
        "        const centerX = candidate.x + candidate.width / 2;"
        "        const centerY = candidate.y + candidate.height / 2;"
        "        return candidate.framePath === item.framePath &&"
        "          candidate.x >= nodeX - 1 && candidate.y >= nodeY - 1 &&"
        "          candidate.x + candidate.width <= right + 1 &&"
        "          candidate.y + candidate.height <= bottom + 1;"
        "      });"
        "      if (contained.length) {"
        "        contained.sort((left, right) =>"
        "          (left.width * left.height) - (right.width * right.height));"
        "        onsale = contained[0];"
        "      }"
        "    }"
        "    const className = String(node.className || '');"
        "    const looksLikeCommunityCard = className.includes('xq-box') || rect.height >= 100;"
        "    if (!onsale && looksLikeCommunityCard && normalized.includes('当前在售房源')) {"
        "      /* WMPF 某些版本把 action 文本绘制在不可遍历节点中；"
        "         入口稳定位于小区卡片底部，使用卡片实时矩形避免写死页面坐标。 */"
        "      onsale = {"
        "        x: rect.left + rect.width / 2 - Math.min(120, rect.width * 0.32),"
        "        y: rect.bottom - Math.min(58, rect.height * 0.21),"
        "        width: Math.min(240, rect.width * 0.64), height: Math.min(58, rect.height * 0.21),"
        "        tag: 'INFERRED_ACTION', className: '当前在售房源', inferred: true"
        "      };"
        "    }"
        "    candidates.push({"
        "      text, normalized, exact: normalized === target,"
        "      tag: String(node.tagName || ''), className: String(node.className || ''),"
        "      x: nodeX, y: nodeY,"
        "      width: rect.width, height: rect.height, framePath: item.framePath, onsale"
        "    });"
        "  }"
        "}"
        "return candidates;"
        "})()"
    )
    response = await client.command(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
        timeout=5.0,
    )
    value = (
        (response or {}).get("result", {}).get("result", {}).get("value")
    )
    if not isinstance(value, list):
        return []
    candidates = [item for item in value if isinstance(item, dict)]
    if is_debug_mode():
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = DEBUG_DIR / "xzsfbj_ui_community_candidates.json"
            path.write_text(
                json.dumps(
                    {"community": community, "candidates": candidates},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            log.info("小区结果候选 DOM 已导出: %s", path)
        except OSError as exc:
            log.warning("小区结果候选 DOM 导出失败: %r", exc)
    return candidates


async def _wait_for_community_detail(
    client: _CdpClient,
    monitor: _NetworkMonitor,
    previous_activity: int,
    community: str,
) -> bool:
    """等待详情页独有的统计 tab，Network 活动可作为兼容兜底。"""
    detail_terms = (community, "租赁成交", "行情统计")
    deadline = asyncio.get_running_loop().time() + 15.0
    while asyncio.get_running_loop().time() < deadline:
        if await _ui_has_terms(client, detail_terms):
            return True
        if monitor.activity_count > previous_activity:
            # 小程序部分版本只发页面框架事件，不把详情文案暴露给 Runtime。
            await asyncio.sleep(random.uniform(0.5, 0.9))
            if monitor.activity_count > previous_activity:
                return True
        await asyncio.sleep(0.25)
    return False


def _ui_text_normalize(value: object) -> str:
    """归一化小程序候选卡片文本，用于小区名和行政区消歧。"""
    return re.sub(r"[\s\u3000]+", "", str(value or "")).casefold()


def _ui_candidate_matches_district(
    candidate: dict[str, object],
    administrative_district: str,
) -> bool:
    """只在候选卡片自身文本中匹配行政区，不读取整页或其它房源文本。"""
    district = _ui_text_normalize(administrative_district)
    card_text = _ui_text_normalize(candidate.get("text"))
    return bool(district and district in card_text)


async def _tap_first_community_result(
    client: _CdpClient,
    monitor: _NetworkMonitor,
    input_mode: str,
    community: str,
    administrative_district: str | None = None,
) -> tuple[tuple[float, float] | None, bool, bool]:
    """定位目标小区；行政区不匹配时不点击并返回 no-match。"""
    previous_activity = monitor.activity_count
    target: list[dict[str, object]] = []
    for attempt in range(12):
        target = await _read_ui_community_candidates(client, community)
        cards = [
            item
            for item in target
            if item.get("onsale") is not None
        ]
        if cards:
            break
        if attempt < 11:
            await asyncio.sleep(random.uniform(0.18, 0.35))
    if administrative_district:
        district_candidates = [
            item
            for item in target
            if _ui_candidate_matches_district(item, administrative_district)
        ]
        log.info(
            "按行政区筛选小区结果：目标=%s，候选=%d，命中=%d，行政区=%s",
            community,
            len(target),
            len(district_candidates),
            administrative_district,
        )
        if not district_candidates:
            log.warning(
                "搜索结果中没有匹配行政区的小区：小区=%s，行政区=%s；不点击，返回首页",
                community,
                administrative_district,
            )
            return None, False, False
        target = district_candidates
    if target:
        cards = [
            item for item in target
            if item.get("onsale") is not None
        ]
        exact = [item for item in cards if item.get("exact")]
        candidates = exact or cards
        if candidates:
            card = min(
                candidates,
                key=lambda item: (
                    float(item.get("y") or 0),
                    float(item.get("x") or 0),
                ),
            )
            onsale = card.get("onsale")
            if not isinstance(onsale, dict):
                raise RuntimeError("小区卡片未返回当前在售房源按钮坐标")
            point = _ui_candidate_point(onsale)
            log.info(
                "通过 DOM 找到目标小区卡片内的当前在售房源入口 "
                "point=(%.0f,%.0f) card=(%.0f,%.0f,%.0f,%.0f) frame=%s",
                point[0],
                point[1],
                float(card.get("x") or 0),
                float(card.get("y") or 0),
                float(card.get("width") or 0),
                float(card.get("height") or 0),
                card.get("framePath", "top"),
            )
            if not await _ui_tap(client, *point, input_mode):
                raise RuntimeError("未能发送当前在售房源入口点击事件")
            return point, True, True

        exact = [item for item in target if item.get("exact")]
        candidates = exact or target
        candidate = min(
            candidates,
            key=lambda item: (
                float(item.get("y") or 0),
                float(item.get("x") or 0),
            ),
        )
        point = _ui_candidate_point(candidate)
        log.info(
            "通过 DOM 找到首条小区结果 point=(%.0f,%.0f) text=%s frame=%s",
            point[0],
            point[1],
            candidate.get("text", ""),
            candidate.get("framePath", "top"),
        )
        if not await _ui_tap(client, *point, input_mode):
            raise RuntimeError("未能发送 DOM 小区结果点击事件")
        if await _wait_for_community_detail(
            client,
            monitor,
            previous_activity,
            community,
        ):
            log.info("DOM 小区结果点击已进入小区详情页")
            return point, False, True
        raise RuntimeError(
            "DOM 小区结果点击后未进入详情页，未继续点击在售房源；"
            "请使用 --debug 检查当前页面"
        )

    if administrative_district:
        log.warning(
            "行政区已指定但当前结果无法定位目标卡片：小区=%s，行政区=%s；不点击",
            community,
            administrative_district,
        )
        return None, False, False

    candidates = (
        ("无统计提示条", DEFAULT_RESULT_POINT),
        ("有统计提示条", DEFAULT_RESULT_POINT_WITH_NOTICE),
    )
    for index, (layout_name, point) in enumerate(candidates):
        log.info(
            "尝试点击第一条小区结果：布局=%s point=(%.0f,%.0f)",
            layout_name,
            point[0],
            point[1],
        )
        if not await _ui_tap(client, *point, input_mode):
            raise RuntimeError("未能发送第一条小区匹配结果点击事件")

        if await _wait_for_community_detail(
            client,
            monitor,
            previous_activity,
            community,
        ):
            log.info("第一条小区结果点击已触发页面活动，采用布局=%s", layout_name)
            return point, False, True
        if index < len(candidates) - 1:
            log.info("布局=%s 未触发页面活动，切换下一种结果页布局", layout_name)

    raise RuntimeError(
        "固定坐标点击后仍未进入小区详情页；"
        "请使用 --debug 检查搜索结果 iframe DOM"
    )


async def _human_type(client: _CdpClient, text: str) -> bool:
    """逐字输入搜索词；中文使用 insertText，ASCII 使用键盘事件。"""
    for char in text:
        if char.isascii() and char.isprintable():
            response = await client.command(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": char,
                    "text": char,
                    "unmodifiedText": char,
                },
            )
            if response is None or "error" in response:
                return False
            response = await client.command(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": char},
            )
        else:
            response = await client.command("Input.insertText", {"text": char})
        if response is None or "error" in response:
            return False
        await asyncio.sleep(random.uniform(0.08, 0.22))
    return True


async def _clear_focused_input(client: _CdpClient) -> bool:
    """清空当前搜索框，避免微信保留上一次查询词导致输入叠加。"""
    events = (
        ("keyDown", "Control", "Control", 17),
        ("keyDown", "a", "KeyA", 65),
        ("keyUp", "a", "KeyA", 65),
        ("keyUp", "Control", "Control", 17),
        ("keyDown", "Backspace", "Backspace", 8),
        ("keyUp", "Backspace", "Backspace", 8),
    )
    for event_type, key, code, virtual_key in events:
        response = await client.command(
            "Input.dispatchKeyEvent",
            {
                "type": event_type,
                "key": key,
                "code": code,
                "windowsVirtualKeyCode": virtual_key,
                "modifiers": 2 if key == "a" else 0,
            },
        )
        if response is None or "error" in response:
            return False
        await asyncio.sleep(random.uniform(0.03, 0.08))
    return True


async def _press_enter(client: _CdpClient) -> bool:
    """提交搜索框，避免只改变输入值而不触发小程序搜索。"""
    down = await client.command(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
    )
    await asyncio.sleep(random.uniform(0.08, 0.16))
    up = await client.command(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
    )
    return all(item is not None and "error" not in item for item in (down, up))


async def _human_swipe(
    client: _CdpClient,
    x: float,
    center_y: float,
    delta_y: float,
) -> bool:
    """发送带多段轨迹、速度变化和轻微横向漂移的真人滑动。"""
    actual_delta = delta_y * random.uniform(0.78, 1.12)
    start_y = center_y + actual_delta / 2
    end_y = center_y - actual_delta / 2
    start_x = x + random.uniform(-12.0, 12.0)
    end_x = start_x + random.uniform(-18.0, 18.0)
    steps = random.randint(11, 18)
    responses: list[dict | None] = []
    for index in range(steps + 1):
        ratio = index / steps
        eased = ratio * ratio * (3.0 - 2.0 * ratio)
        point_x = start_x + (end_x - start_x) * eased
        point_y = start_y + (end_y - start_y) * eased
        event_type = "touchStart" if index == 0 else "touchMove"
        responses.append(await client.command(
            "Input.dispatchTouchEvent",
            {
                "type": event_type,
                "touchPoints": [{
                    "id": 1,
                    "x": point_x,
                    "y": point_y,
                    "radiusX": random.uniform(6.0, 10.0),
                    "radiusY": random.uniform(6.0, 10.0),
                    "force": random.uniform(0.55, 0.85),
                }],
                "modifiers": 0,
            },
        ))
        if index < steps:
            await asyncio.sleep(random.uniform(0.025, 0.075))
    responses.append(await client.command(
        "Input.dispatchTouchEvent",
        {"type": "touchEnd", "touchPoints": [], "modifiers": 0},
    ))
    await asyncio.sleep(random.uniform(0.7, 1.5))
    return all(item is not None and "error" not in item for item in responses)


async def _page_screenshot_signature(client: _CdpClient) -> str | None:
    """获取当前小程序视口截图指纹，用于识别滑动到边界。"""
    response = await client.command(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True},
        timeout=5.0,
    )
    data = (response or {}).get("result", {}).get("data")
    if not isinstance(data, str) or not data:
        return None
    try:
        return hashlib.sha256(base64.b64decode(data)).hexdigest()
    except (ValueError, TypeError):
        return None


async def _ui_has_terms(
    client: _CdpClient,
    terms: tuple[str, ...],
) -> bool | None:
    """Check visible text and focused input value without returning page contents."""
    encoded_terms = json.dumps(terms, ensure_ascii=False)
    response = await client.command(
        "Runtime.evaluate",
        {
            "expression": (
                "(() => {"
                "const terms = " + encoded_terms + ";"
                "const documents = [document];"
                "for (let index = 0; index < documents.length; index++) {"
                "  const current = documents[index];"
                "  for (const frame of Array.from(current.querySelectorAll('iframe'))) {"
                "    if (frame.getAttribute('data-visibility') === 'hidden') continue;"
                "    try { if (frame.contentDocument) documents.push(frame.contentDocument); } catch (_) {}"
                "  }"
                "}"
                "const text = documents.map(current => {"
                "  const body = String(current.body && current.body.innerText || '');"
                "  const active = current.activeElement;"
                "  return body + '\\n' + String(active && active.value || '');"
                "}).join('\\n');"
                "return terms.every(term => text.includes(term));"
                "})()"
            ),
            "returnByValue": True,
        },
    )
    if response is None or "error" in response:
        return None
    value = response.get("result", {}).get("result", {}).get("value")
    return value if isinstance(value, bool) else None


_SEARCH_INPUT_DOM_EXPRESSION = r"""
(() => {
  const documents = [];
  const visited = new Set();
  const candidates = [];
  const viewportWidth = Number(window.innerWidth || document.documentElement.clientWidth || 0);
  const viewportHeight = Number(window.innerHeight || document.documentElement.clientHeight || 0);
  const intersectsViewport = rect => rect.right > 0 && rect.bottom > 0 &&
    rect.left < viewportWidth && rect.top < viewportHeight;
  const isVisible = (node, view) => {
    const rect = node.getBoundingClientRect();
    const style = view.getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && intersectsViewport(rect) &&
      style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  };
  const collectDocument = (doc, offsetX, offsetY, framePath) => {
    if (!doc || visited.has(doc)) return;
    visited.add(doc);
    documents.push({doc, offsetX, offsetY, framePath});
    const view = doc.defaultView || window;
    for (const [index, frame] of Array.from(doc.querySelectorAll('iframe')).entries()) {
      if (frame.getAttribute('data-visibility') === 'hidden') continue;
      let child = null;
      try { child = frame.contentDocument; } catch (_) {}
      if (!child || !isVisible(frame, view)) continue;
      const rect = frame.getBoundingClientRect();
      collectDocument(
        child,
        offsetX + rect.left,
        offsetY + rect.top,
        framePath + '.iframe[' + index + ']'
      );
    }
  };

  collectDocument(document, 0, 0, 'top');
  const selector = 'input:not([type="hidden"]), textarea, [contenteditable="true"]';
  for (const item of documents) {
    const view = item.doc.defaultView || window;
    for (const node of Array.from(item.doc.querySelectorAll(selector))) {
      if (!isVisible(node, view)) continue;
      const rect = node.getBoundingClientRect();
      candidates.push({
        x: rect.left + item.offsetX,
        y: rect.top + item.offsetY,
        width: rect.width,
        height: rect.height,
        tag: String(node.tagName || ''),
        type: String(node.getAttribute('type') || ''),
        placeholder: String(node.getAttribute('placeholder') || ''),
        value: String(node.value ?? node.textContent ?? ''),
        framePath: item.framePath,
      });
    }
  }
  return candidates;
})()
"""


async def _read_ui_search_inputs(
    client: _CdpClient,
) -> list[dict[str, object]] | None:
    """读取当前可见搜索输入框，坐标统一换算为小程序视口坐标。"""
    response = await client.command(
        "Runtime.evaluate",
        {
            "expression": _SEARCH_INPUT_DOM_EXPRESSION,
            "returnByValue": True,
        },
        timeout=5.0,
    )
    value = (
        (response or {}).get("result", {}).get("result", {}).get("value")
    )
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


async def _wait_for_ui_search_inputs(
    client: _CdpClient,
    report_interval: float = 5.0,
) -> list[dict[str, object]] | None:
    """等待当前小程序真正挂载可搜索页面，不把加载过程误判成失败。"""
    last_report = asyncio.get_running_loop().time()
    probe_count = 0
    unreadable_count = 0
    while True:
        inputs = await _read_ui_search_inputs(client)
        probe_count += 1
        if inputs:
            return inputs
        if inputs is None:
            unreadable_count += 1
            if unreadable_count >= 3:
                log.warning(
                    "连续%d次无法读取搜索框 DOM，改用当前窗口默认坐标",
                    unreadable_count,
                )
                return None
        else:
            unreadable_count = 0

        now = asyncio.get_running_loop().time()
        if now - last_report >= report_interval:
            state = "Runtime.evaluate 暂不可读" if inputs is None else "搜索框尚未挂载"
            log.info(
                "仍在等待小程序进入可搜索状态：状态=%s，DOM探测=%d；"
                "请保持微信小程序在前台",
                state,
                probe_count,
            )
            last_report = now
        await asyncio.sleep(0.5)


async def _ui_focused_search_input_value(
    client: _CdpClient,
) -> str | None:
    """读取当前 iframe 中已获得焦点的搜索输入值；未聚焦时返回 None。"""
    response = await client.command(
        "Runtime.evaluate",
        {
            "expression": (
                "(() => {"
                "const selector = 'input:not([type=hidden]), textarea, [contenteditable=true]';"
                "const documents = [document];"
                "for (let index = 0; index < documents.length; index++) {"
                "  const current = documents[index];"
                "  for (const frame of Array.from(current.querySelectorAll('iframe'))) {"
                "    if (frame.getAttribute('data-visibility') === 'hidden') continue;"
                "    try { if (frame.contentDocument) documents.push(frame.contentDocument); } catch (_) {}"
                "  }"
                "}"
                "for (const current of documents) {"
                "  const active = current.activeElement;"
                "  if (active && active.matches && active.matches(selector)) {"
                "    return String(active.value ?? active.textContent ?? '');"
                "  }"
                "}"
                "return null;"
                "})()"
            ),
            "returnByValue": True,
        },
        timeout=5.0,
    )
    if response is None or "error" in response:
        return None
    value = response.get("result", {}).get("result", {}).get("value")
    return value if isinstance(value, str) else None


async def _ui_has_visible_search_input(
    client: _CdpClient,
) -> bool | None:
    """判断当前小程序是否已经回到 MVP 可继续搜索的小区搜索页。"""
    return await _ui_has_terms(client, ("小区搜索",))


async def _wait_for_visible_search_input(
    client: _CdpClient,
    attempts: int = 12,
) -> bool | None:
    """等待返回动画结束后再判断搜索框，避免过早进入下一条记录。"""
    saw_probe = False
    for _ in range(attempts):
        state = await _ui_has_visible_search_input(client)
        saw_probe = saw_probe or state is not None
        if state is True:
            return True
        await asyncio.sleep(0.25)
    return False if saw_probe else None


_AREA_DOM_EXPRESSION = r"""
(() => {
  const documents = [];
  const visited = new Set();

  const collectDocument = (doc, offsetX, offsetY, framePath) => {
    if (!doc || visited.has(doc)) return;
    visited.add(doc);
    documents.push({doc, offsetX, offsetY, framePath});
    for (const [index, frame] of Array.from(doc.querySelectorAll('iframe')).entries()) {
      let child = null;
      try { child = frame.contentDocument; } catch (_) {}
      if (!child) continue;
      const rect = frame.getBoundingClientRect();
      collectDocument(
        child,
        offsetX + rect.left,
        offsetY + rect.top,
        framePath + '.iframe[' + index + ']'
      );
    }
  };

  collectDocument(document, 0, 0, 'top');
  const controls = [];
  const options = [];
  for (const item of documents) {
    const view = item.doc.defaultView || window;
    for (const node of Array.from(item.doc.querySelectorAll('*'))) {
      const text = String(node.textContent || '').replace(/\s+/g, ' ').trim();
      const normalized = text.replace(/\s+/g, '');
      const rect = node.getBoundingClientRect();
      if (!text || rect.width <= 0 || rect.height <= 0) continue;
      const style = view.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') continue;

      const className = String(node.className || '');
      const ariaDisabled = node.getAttribute('aria-disabled') === 'true';
      const disabled = node.hasAttribute('disabled') || ariaDisabled ||
        /disabled|disable|muted/i.test(className) ||
        style.pointerEvents === 'none';
      const record = {
        text,
        normalized,
        tag: String(node.tagName || ''),
        className,
        style: String(node.getAttribute('style') || ''),
        color: String(style.color || ''),
        backgroundColor: String(style.backgroundColor || ''),
        disabled,
        x: rect.left + item.offsetX,
        y: rect.top + item.offsetY,
        width: rect.width,
        height: rect.height,
        framePath: item.framePath,
      };

      if (/^面积(?:[↑↓↕⌃⌄])?$/.test(normalized)) {
        controls.push(record);
      }
      if (/\d/.test(normalized) && /(?:㎡|m²|m2|平方米|平米|平方|平)/i.test(normalized) &&
          normalized.length <= 40) {
        options.push(record);
      }
    }
  }
  return {controls, options, documentCount: documents.length};
})()
"""


async def _read_ui_area_dom(
    client: _CdpClient,
    debug_name: str | None = None,
) -> dict[str, object]:
    """读取面积控件和档位坐标，兼容 WMPF 主文档及同源 iframe。"""
    response = await client.command(
        "Runtime.evaluate",
        {
            "expression": _AREA_DOM_EXPRESSION,
            "returnByValue": True,
        },
        timeout=5.0,
    )
    snapshot = (
        (response or {}).get("result", {}).get("result", {}).get("value")
    )
    if not isinstance(snapshot, dict):
        return {}

    if debug_name and is_debug_mode():
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = DEBUG_DIR / f"xzsfbj_ui_{debug_name}_area.json"
            path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("面积控件 DOM 已导出: %s", path)
        except OSError as exc:
            log.warning("面积控件 DOM 导出失败 name=%s: %r", debug_name, exc)
    return snapshot


def _parse_ui_area_option(candidate: dict[str, object]) -> dict[str, object] | None:
    """将一个 DOM 文本候选转换为面积范围和在售数量。"""
    text = re.sub(r"\s+", "", str(candidate.get("text") or ""))
    if not text:
        return None

    matched = _AREA_RANGE_PATTERN.search(text)
    minimum: float
    maximum: float | None
    if matched:
        minimum = float(matched.group("minimum"))
        maximum = float(matched.group("maximum"))
    else:
        matched = _AREA_UPPER_PATTERN.search(text)
        if matched:
            minimum = float(matched.group("minimum"))
            maximum = None
        else:
            matched = _AREA_LOWER_PATTERN.search(text)
            if not matched:
                return None
            minimum = 0.0
            maximum = float(matched.group("maximum"))

    count_match = re.search(r"[\(（]\s*(\d+)\s*[\)）]", text)
    if count_match is None and matched is not None:
        tail = text[matched.end():]
        count_match = re.match(r"\s*[:：]?\s*(\d+)\s*(?:套|套在售)?$", tail)
    count = int(count_match.group(1)) if count_match else None
    return {
        **candidate,
        "label": text,
        "minimum": minimum,
        "maximum": maximum,
        "count": count,
        "disabled": bool(candidate.get("disabled")) or count == 0,
    }


def _unique_ui_area_options(snapshot: dict[str, object]) -> list[dict[str, object]]:
    """去掉同一按钮的父子 DOM 重复节点，保留可点击面积最大的节点。"""
    raw_options = snapshot.get("options")
    if not isinstance(raw_options, list):
        return []
    grouped: dict[tuple[float, float | None], list[dict[str, object]]] = {}
    for candidate in raw_options:
        if not isinstance(candidate, dict):
            continue
        parsed = _parse_ui_area_option(candidate)
        if parsed is None:
            continue
        range_key = (float(parsed["minimum"]), parsed["maximum"])
        grouped.setdefault(range_key, []).append(parsed)

    options: list[dict[str, object]] = []
    for candidates in grouped.values():
        selected = max(
            candidates,
            key=lambda item: (
                item.get("count") is not None,
                bool(item.get("disabled")),
                float(item.get("width") or 0)
                * float(item.get("height") or 0),
            ),
        )
        options.append(selected)
    return sorted(
        options,
        key=lambda item: (
            float(item.get("minimum") or 0),
            float(item.get("maximum") or float("inf")),
        ),
    )


def _select_ui_area_option(
    options: list[dict[str, object]],
    area: float,
) -> dict[str, object] | None:
    """按页面实际档位匹配请求面积，不假设每个小区有相同档位。"""
    matches = []
    for option in options:
        minimum = float(option["minimum"])
        maximum = option.get("maximum")
        if area < minimum:
            continue
        if maximum is not None and area > float(maximum):
            continue
        matches.append(option)
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (
            float(item.get("maximum") or float("inf"))
            - float(item.get("minimum") or 0),
            float(item.get("minimum") or 0),
        ),
    )


def _ui_candidate_point(candidate: dict[str, object]) -> tuple[float, float]:
    """返回 DOM 候选的视口中心坐标。"""
    x = float(candidate.get("x") or 0)
    y = float(candidate.get("y") or 0)
    width = float(candidate.get("width") or 0)
    height = float(candidate.get("height") or 0)
    return x + width / 2, y + height / 2


async def _apply_ui_area_filter(
    client: _CdpClient,
    monitor: _NetworkMonitor,
    area: float,
    input_mode: str,
) -> tuple[float, float | None] | None:
    """打开面积菜单，动态选择命中档位；无房源档位时返回 None。"""
    snapshot = await _read_ui_area_dom(client, "area_filter_before")
    controls = snapshot.get("controls") if isinstance(snapshot, dict) else None
    control_candidates = [item for item in controls or [] if isinstance(item, dict)]
    if control_candidates:
        control = max(
            control_candidates,
            key=lambda item: float(item.get("width") or 0)
            * float(item.get("height") or 0),
        )
        control_point = _ui_candidate_point(control)
        log.info(
            "通过 DOM 找到面积筛选按钮 point=(%.0f,%.0f) text=%s frame=%s",
            control_point[0],
            control_point[1],
            control.get("text", ""),
            control.get("framePath", "top"),
        )
    else:
        control_point = DEFAULT_AREA_FILTER_POINT
        log.warning(
            "DOM 未找到面积筛选按钮，使用当前窗口布局兜底坐标 point=(%.0f,%.0f)",
            control_point[0],
            control_point[1],
        )

    if not await _ui_tap(client, *control_point, input_mode):
        raise RuntimeError("未能点击面积筛选按钮")

    options: list[dict[str, object]] = []
    for _ in range(32):
        await asyncio.sleep(0.25)
        snapshot = await _read_ui_area_dom(client)
        options = _unique_ui_area_options(snapshot)
        if options:
            break

    if not options:
        await _read_ui_area_dom(client, "area_filter_unreadable")
        raise RuntimeError(
            "面积筛选菜单已打开，但未从 DOM 读取到面积档位；"
            "请使用 --debug 检查 xzsfbj_ui_area_filter_unreadable_area.json"
        )

    log.info(
        "读取到面积档位：%s",
        [
            {
                "label": option["label"],
                "count": option["count"],
                "disabled": option["disabled"],
            }
            for option in options
        ],
    )
    target = _select_ui_area_option(options, area)
    if target is None:
        log.warning(
            "请求面积 %.2f㎡ 未匹配到页面面积档位，安全返回首页；档位=%s",
            area,
            [option["label"] for option in options],
        )
        return None

    log.info(
        "请求面积 %.2f㎡ 命中面积档位=%s，数量=%s，disabled=%s",
        area,
        target["label"],
        target["count"],
        target["disabled"],
    )
    if target["disabled"] or target.get("count") == 0:
        log.info(
            "面积档位=%s 已置灰或数量为 0，不点击，不采集，返回首页",
            target["label"],
        )
        return None

    target_point = _ui_candidate_point(target)
    if not await _ui_tap(client, *target_point, input_mode):
        raise RuntimeError(f"未能点击面积档位: {target['label']}")
    log.info(
        "面积档位已点击 point=(%.0f,%.0f)，点击后在售响应=%d",
        target_point[0],
        target_point[1],
        monitor.response_count(constants.API_SALES_PATH),
    )
    await asyncio.sleep(random.uniform(0.8, 1.4))
    log.info(
        "面积筛选已完成，当前在售响应=%d；后续仅在售列表滚动并由 Network 采集",
        monitor.response_count(constants.API_SALES_PATH),
    )
    return float(target["minimum"]), (
        float(target["maximum"]) if target.get("maximum") is not None else None
    )


async def _return_to_home_from_ui(
    client: _CdpClient,
    input_mode: str,
    reason: str,
) -> None:
    """返回到可搜索页面；必要时再退一层，避免下一条沿用旧页面。"""
    log.info("%s，自动返回小程序可搜索页面", reason)
    # 搜索结果页也包含“小区搜索”和输入框。一次返回只能退到结果页，
    # 此时继续复用输入框容易留下上一条关键词；批量流程固定至少退两层。
    minimum_back_presses = 2
    max_back_presses = 4
    for attempt in range(1, max_back_presses + 1):
        if attempt == 2:
            log.info("已执行一次返回但仍可能停留在搜索结果页，继续第二次返回")
        elif attempt > minimum_back_presses:
            log.info("已返回%d次仍未确认可搜索页面，继续尝试返回", attempt - 1)
        if not await _ui_tap(client, *DEFAULT_BACK_POINT, input_mode):
            raise RuntimeError(f"未能发送第{attempt}次返回点击事件")
        state = await _wait_for_visible_search_input(client)
        if attempt >= minimum_back_presses and state is True:
            log.info(
                "已至少返回%d次并确认回到可搜索页面，下一条记录可直接输入关键词",
                attempt,
            )
            return
        if state is None:
            log.warning("返回%d次后无法通过 DOM 判断搜索框状态", attempt)

    raise RuntimeError("返回后未确认小程序处于可搜索页面，请使用 --debug 检查当前页面")


async def _wait_for_ui_terms(
    client: _CdpClient,
    terms: tuple[str, ...],
    timeout: float | None = None,
) -> bool:
    """等待页面状态；默认一直等到状态真实出现。"""
    deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
    saw_probe = False
    while deadline is None or asyncio.get_running_loop().time() < deadline:
        state = await _ui_has_terms(client, terms)
        if state is True:
            return True
        saw_probe = saw_probe or state is not None
        await asyncio.sleep(0.25)
    if not saw_probe:
        log.warning("UI 页面状态无法通过 Runtime.evaluate 读取，terms=%s", terms)
    return False


async def _enable_network_after_manual_ready(client: _CdpClient) -> None:
    """在人工确认小程序已打开后启用 Network，避免桥接丢弃早发命令。"""
    while True:
        response = await client.command("Network.enable")
        if response is not None and "error" not in response:
            log.info("Network 监听已启用")
            return
        log.warning("Network.enable 未成功，请确认小程序仍在前台")
        await asyncio.to_thread(input, "确认微信小程序已打开后按回车重试 Network.enable: ")


def _captured_sales_to_result(
    monitor: _NetworkMonitor,
    community: str,
    area: float,
    ui_area_range: tuple[float, float | None] | None = None,
) -> PlatformResult:
    """将 UI 期间捕获的 getCommunitySales 响应转成统一 MVP 结果。

    UI 面积筛选使用小程序实际展示的档位范围；普通调用没有该范围时，
    继续使用统一的精确面积过滤逻辑。
    """
    raw_sales: list[dict] = []
    seen_ids: set[str] = set()
    for response in monitor.responses:
        if response.path != constants.API_SALES_PATH or not isinstance(response.payload, dict):
            continue
        data = response.payload.get("data")
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            house_id = str(item.get("id") or item.get("houseId") or "")
            if house_id and house_id in seen_ids:
                continue
            if house_id:
                seen_ids.add(house_id)
            raw_sales.append(item)

    raw_snapshots = parse_listing_snapshots(raw_sales, community)
    if raw_snapshots:
        available_areas = [
            snapshot.area
            for snapshot in raw_snapshots
            if snapshot.area is not None
        ]
        area_range = (
            f"{min(available_areas):.2f}~{max(available_areas):.2f}㎡"
            if available_areas
            else "未知"
        )
        last_snapshot = raw_snapshots[-1]
        log.info(
            "Network 在售原始数据已解析：%d 条，面积范围=%s，最后一条房源="
            "id=%s 面积=%s㎡ 单价=%s元/㎡",
            len(raw_snapshots),
            area_range,
            last_snapshot.house_id,
            last_snapshot.area,
            last_snapshot.unit_price,
        )
    if ui_area_range is None:
        snapshots, quote_prices, reference = prepare_listing_data_with_reference(
            raw_snapshots, community, area
        )
    else:
        area_min, area_max = ui_area_range
        community_snapshots, _, _ = prepare_listing_data_with_reference(
            raw_snapshots, community, None
        )
        snapshots = [
            snapshot
            for snapshot in community_snapshots
            if snapshot.area is not None
            and snapshot.area >= area_min
            and (area_max is None or snapshot.area <= area_max)
        ]
        quote_prices = [
            snapshot.unit_price
            for snapshot in snapshots
            if snapshot.unit_price is not None and snapshot.unit_price > 0
        ]
        reference = {}
        log.info(
            "按小程序面积档位过滤：范围=%.2f~%s㎡，命中=%d 条",
            area_min,
            f"{area_max:.2f}" if area_max is not None else "以上",
            len(snapshots),
        )
    if not snapshots:
        if ui_area_range is None:
            status = listing_no_data_status(raw_snapshots, community, area)
            reason = listing_no_data_reason(raw_snapshots, community, area)
        else:
            area_min, area_max = ui_area_range
            status = PlatformResultStatus.NO_MATCHING_AREA
            reason = (
                f"已点击面积档位但响应中没有可解析房源: "
                f"{area_min:.2f}~{area_max:.2f}㎡"
                if area_max is not None
                else f"已点击面积档位但响应中没有可解析房源: {area_min:.2f}㎡以上"
            )
        log.warning(
            "Network 已采集到在售房源，但面积过滤没有命中：原始=%d 条，"
            "请求面积=%.2f㎡，UI档位=%s，状态=%s，原因=%s",
            len(raw_snapshots),
            area,
            ui_area_range,
            status,
            reason,
        )
        # MVP 调试时保留原始快照，避免把“采集成功但面积不匹配”误报成“没有数据”。
        # quote_prices 仍为空，因此不会进入最终估值算法。
        return PlatformResult(
            name="行舟深房",
            status=status,
            reason=reason,
            request_id="ui-mvp",
            elapsed_seconds=0.0,
            listing_snapshots=raw_snapshots,
        )
    return PlatformResult(
        name="行舟深房",
        status=PlatformResultStatus.SUCCESS,
        quote_prices=quote_prices,
        listing_snapshots=snapshots,
        deal_source="无",
        request_id="ui-mvp",
        reference_code=reference.get("reference_code"),
        reference_area_tolerance=reference.get("reference_area_tolerance"),
        reference_area_min=reference.get("reference_area_min"),
        reference_area_max=reference.get("reference_area_max"),
        reference_listing_count=reference.get("reference_listing_count"),
    )


async def _run_ui_mvp_item(
    client: _CdpClient,
    monitor: _NetworkMonitor,
    community: str,
    area: float,
    scroll_rounds: int,
    input_mode: str,
    administrative_district: str | None,
    hold_seconds: float = 0.0,
) -> PlatformResult:
    """执行一条完整 UI 流程；调用方负责桥接生命周期。"""
    monitor.reset_capture()
    await _dump_ui_dom(
        client,
        "ready",
        terms=(community, "当前在售房源"),
    )

    activity_before = monitor.activity_count
    # 批量模式复用 MVP 已验证的输入动作；批量新增的只有外层循环和桥接复用。
    log.info("准备点击首页搜索框，小区=%s", community)
    if not await _ui_tap(client, *DEFAULT_SEARCH_POINT, input_mode):
        raise RuntimeError("未能发送首页搜索框点击事件")
    log.info("搜索框点击完成，准备清空旧关键词")
    if not await _clear_focused_input(client):
        raise RuntimeError("未能清空搜索框")
    log.info("旧关键词已清空，准备输入小区=%s", community)
    if not await _human_type(client, community):
        raise RuntimeError("未能发送搜索关键词输入事件")
    log.info("小区关键词输入完成，准备提交搜索=%s", community)
    if not await _press_enter(client):
        raise RuntimeError("未能提交小区搜索")
    await monitor.wait_for_activity(activity_before)
    log.info("搜索请求已发生，准备选择小区结果")
    await _dump_ui_dom(
        client,
        "search_results",
        terms=(community, "当前在售房源"),
    )

    log.info("准备自动点击第一条小区结果，目标=%s", community)
    sales_before = monitor.response_count(constants.API_SALES_PATH)
    _, direct_onsale, district_matched = await _tap_first_community_result(
        client,
        monitor,
        input_mode,
        community,
        administrative_district,
    )
    if not district_matched:
        reason = f"搜索结果中没有行政区为 {administrative_district} 的小区 {community}"
        no_data_result = PlatformResult(
            name="行舟深房",
            status=PlatformResultStatus.NO_DATA,
            reason=reason,
            request_id="ui-mvp",
            elapsed_seconds=0.0,
        )
        await _return_to_home_from_ui(client, input_mode, reason)
        _print_result(community, area, no_data_result)
        return no_data_result
    if direct_onsale:
        log.info(
            "已从搜索结果卡片直接点击当前在售房源，等待小程序 Network 响应；"
            "点击前在售响应=%d",
            sales_before,
        )
    else:
        log.info("小区结果页已发生页面活动，准备进入在售房源")
        await _dump_ui_dom(
            client,
            "community_detail",
            terms=(community, "当前在售房源"),
        )
        log.info(
            "通过详情页点击在售房源入口 point=(%.0f,%.0f)，点击前在售响应=%d",
            DEFAULT_ONSALE_POINT[0],
            DEFAULT_ONSALE_POINT[1],
            sales_before,
        )
        if not await _ui_tap(client, *DEFAULT_ONSALE_POINT, input_mode):
            raise RuntimeError("未能发送在售房源点击事件")
        log.info("详情页在售房源入口点击已发送，等待小程序 Network 响应")
    first_sales = await monitor.wait_for_response(
        constants.API_SALES_PATH, sales_before
    )
    first_count = (
        len(first_sales.payload.get("data", []))
        if isinstance(first_sales.payload, dict)
        and isinstance(first_sales.payload.get("data"), list)
        else 0
    )
    log.info(
        "已进入在售列表，首批 Network 数据=%d 条，累计去重房源=%d，准备面积筛选",
        first_count,
        monitor.unique_sales_count(),
    )
    await _dump_ui_dom(
        client,
        "onsale_list",
        terms=(community, "当前在售房源"),
    )

    ui_area_range = await _apply_ui_area_filter(
        client,
        monitor,
        area,
        input_mode,
    )
    if ui_area_range is None:
        no_data_reason = f"请求面积 {area:.2f}㎡ 没有可用在售档位"
        await _return_to_home_from_ui(
            client,
            input_mode,
            no_data_reason,
        )
        # 面积档位置灰或数量为 0 是正常的面积不匹配结果；仍需向
        # 批量调用方交付结构化结果，不能被误判为桥接异常。
        no_matching_area_result = PlatformResult(
            name="行舟深房",
            status=PlatformResultStatus.NO_MATCHING_AREA,
            reason=no_data_reason,
            request_id="ui-mvp",
            elapsed_seconds=0.0,
        )
        _print_result(community, area, no_matching_area_result)
        return no_matching_area_result
    await _dump_ui_dom(
        client,
        "onsale_area_filtered",
        terms=(community, "当前在售房源"),
    )
    log.info("面积筛选完成，开始真人滚动并监听后续 Network")

    previous_signature = await _page_screenshot_signature(client)
    if previous_signature is None:
        log.warning("无法获取小程序截图指纹，滚动边界将回退到轮数上限")

    reached_bottom = False
    # 轮数是探索期的安全上限，不是页面加载超时；截图不再变化时提前结束。
    for index in range(1, scroll_rounds + 1):
        if not await _human_swipe(
            client,
            DEFAULT_SCROLL_X,
            DEFAULT_SCROLL_CENTER_Y,
            DEFAULT_SCROLL_DELTA,
        ):
            raise RuntimeError(f"第{index}次真人滑动未收到完整 CDP 响应")

        current_signature = await _page_screenshot_signature(client)
        if (
            previous_signature is not None
            and current_signature is not None
            and current_signature == previous_signature
        ):
            log.info(
                "真人滑动第%d次后页面截图无变化，判定已到在售列表底部，停止继续滑动",
                index,
            )
            reached_bottom = True
            break
        if current_signature is not None:
            previous_signature = current_signature

        if monitor.response_count(constants.API_SALES_PATH) > sales_before:
            latest = monitor.responses[-1]
            if latest.path == constants.API_SALES_PATH:
                data = latest.payload.get("data") if isinstance(latest.payload, dict) else None
                log.info(
                    "真人滑动第%d次触发在售响应，数据=%s",
                    index,
                    len(data) if isinstance(data, list) else "-",
                )
            sales_before = monitor.response_count(constants.API_SALES_PATH)

    if not reached_bottom and previous_signature is None:
        log.info("截图指纹不可用，真人滑动已达到安全轮数上限=%d", scroll_rounds)
    elif not reached_bottom:
        log.info("真人滑动已达到安全轮数上限=%d，页面仍可能存在未加载区域", scroll_rounds)

    await asyncio.sleep(random.uniform(1.2, 2.4))
    await _return_to_home_from_ui(
        client,
        input_mode,
        "在售列表采集完成",
    )
    result = _captured_sales_to_result(
        monitor,
        community,
        area,
        ui_area_range,
    )
    log.info(
        "UI MVP 已完成：输入 -> 搜索 -> 进入在售 -> 真人滚动 -> 返回；"
        "Network事件=%s，在售响应批次=%d，采集快照=%d，面积命中=%d",
        monitor.event_counts,
        monitor.response_count(constants.API_SALES_PATH),
        len(result.listing_snapshots),
        len(result.quote_prices),
    )
    _print_result(community, area, result)
    if hold_seconds > 0:
        await asyncio.sleep(hold_seconds)
    return result


async def run_ui_mvp(
    community: str,
    area: float,
    hold_seconds: float = 0.0,
    scroll_rounds: int = 40,
    input_mode: str = "mouse",
    administrative_district: str | None = None,
) -> PlatformResult | None:
    """通过小程序 UI 搜索、阅读在售列表、滚动加载并自动返回。"""
    proc = None
    client = None
    monitor_task = None
    stop_event = asyncio.Event()
    try:
        proc = await _start_wmpf_bridge()
        async with websockets.connect(CDP_PROXY_URL) as socket:
            client = _CdpClient(socket)
            await client.start()
            monitor = _NetworkMonitor(client, stop_event)
            monitor_task = asyncio.create_task(monitor.run())
            log.info(
                "UI MVP 已启动 Network 后台监听。请打开行舟深房小程序首页，"
                "停留在可搜索状态后回到终端按回车；目标小区=%s，行政区=%s",
                community,
                administrative_district or "未指定",
            )
            await asyncio.to_thread(input)
            await _enable_network_after_manual_ready(client)
            return await _run_ui_mvp_item(
                client,
                monitor,
                community,
                area,
                scroll_rounds,
                input_mode,
                administrative_district,
                hold_seconds,
            )
    except (OSError, RuntimeError, websockets.WebSocketException) as exc:
        log.exception("UI MVP 失败: %r (%s)", exc, type(exc).__name__)
        return None
    finally:
        stop_event.set()
        if monitor_task is not None and not monitor_task.done():
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if client is not None:
            await client.close()
        await _stop_wmpf_bridge(proc)


async def run_ui_mvp_batch(
    targets: list[tuple[str, float, str | None]],
    scroll_rounds: int = 40,
    input_mode: str = "mouse",
    gap_seconds: float = 2.0,
    auto_ready: bool = False,
    keep_alive: bool = True,
    on_complete: Callable[
        [list[tuple[PlatformResult | None, float]]],
        None,
    ]
    | None = None,
) -> list[tuple[PlatformResult | None, float]]:
    """复用一个桥批量运行 UI MVP；完成后默认驻留等待下一条任务。"""
    proc = None
    client = None
    monitor_task = None
    stop_event = asyncio.Event()
    results: list[tuple[PlatformResult | None, float]] = []
    try:
        proc = await _start_wmpf_bridge()
        async with websockets.connect(CDP_PROXY_URL) as socket:
            client = _CdpClient(socket)
            await client.start()
            monitor = _NetworkMonitor(client, stop_event)
            monitor_task = asyncio.create_task(monitor.run())
            log.info(
                "UI MVP 批量已启动 Network 后台监听：打开行舟深房小程序首页，"
                "停留在可搜索状态后回到终端按回车；共%d条",
                len(targets),
            )
            if not auto_ready:
                await asyncio.to_thread(input)
            await _enable_network_after_manual_ready(client)

            for index, (community, area, administrative_district) in enumerate(
                targets,
                start=1,
            ):
                log.info(
                    "UI MVP 批量第%d/%d条：小区=%s，行政区=%s，面积=%.2f㎡",
                    index,
                    len(targets),
                    community,
                    administrative_district or "未指定",
                    area,
                )
                started = time.perf_counter()
                try:
                    result = await _run_ui_mvp_item(
                        client,
                        monitor,
                        community,
                        area,
                        scroll_rounds,
                        input_mode,
                        administrative_district,
                    )
                except (OSError, RuntimeError, websockets.WebSocketException) as exc:
                    elapsed = round(time.perf_counter() - started, 2)
                    log.exception(
                        "UI MVP 批量第%d/%d条失败：%r (%s)",
                        index,
                        len(targets),
                        exc,
                        type(exc).__name__,
                    )
                    results.append((None, elapsed))
                    break
                elapsed = round(time.perf_counter() - started, 2)
                results.append((result, elapsed))
                log.info(
                    "UI MVP 批量第%d/%d条完成，状态=%s，耗时=%.2f秒；"
                    "已回到可搜索页面，继续下一条",
                    index,
                    len(targets),
                    result.status.value,
                    elapsed,
                )
                if index < len(targets):
                    await asyncio.sleep(max(gap_seconds, 0.0))
            if on_complete is not None:
                on_complete(results)
            if keep_alive:
                log.info(
                    "UI MVP 批量 %d 条已完成；保持微信小程序、WMPF 桥和 Network 监听，"
                    "等待下一条任务（Ctrl+C 才清理）",
                    len(results),
                )
                await asyncio.Event().wait()
    except (OSError, RuntimeError, websockets.WebSocketException) as exc:
        log.exception("UI MVP 批量启动失败：%r (%s)", exc, type(exc).__name__)
    finally:
        stop_event.set()
        if monitor_task is not None and not monitor_task.done():
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if client is not None:
            await client.close()
        await _stop_wmpf_bridge(proc)
    return results


async def run_probe_mvp(duration: float) -> None:
    """在人工打开页面后探测页面能力并观察 Network。"""
    proc = None
    client = None
    network_task = None
    monitor_task = None
    stop_event = asyncio.Event()
    try:
        proc = await _start_wmpf_bridge()
        async with websockets.connect(CDP_PROXY_URL) as socket:
            client = _CdpClient(socket)
            await client.start()
            monitor = _NetworkMonitor(client, stop_event)
            monitor_task = asyncio.create_task(monitor.run())
            log.info(
                "Probe 已启动 Network 后台监听，请打开小程序并进入目标页面，"
                "然后回到终端按回车"
            )
            await asyncio.to_thread(input)
            await _enable_network_after_manual_ready(client)

            page_result = await _probe_page_context(client)
            log.info(
                "页面探测: Runtime=%s DOM=%s text=%s chars=%d",
                page_result["runtime_enabled"],
                page_result["dom_available"],
                page_result["text_readable"],
                page_result["text_length"],
            )
            await _dump_ui_dom(client, "probe")
            log.info("继续监听 Network %.1f 秒...", duration)
            await asyncio.sleep(duration)
            log.info(
                "Network 汇总: %s；在售响应体批次=%d",
                monitor.event_counts,
                monitor.response_count(constants.API_SALES_PATH),
            )
    except (OSError, RuntimeError, websockets.WebSocketException) as exc:
        log.exception("Probe MVP 失败: %r (%s)", exc, type(exc).__name__)
    finally:
        stop_event.set()
        for task in (network_task, monitor_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (network_task, monitor_task) if task is not None),
            return_exceptions=True,
        )
        if client is not None:
            await client.close()
        await _stop_wmpf_bridge(proc)


async def run_scroll_mvp(
    rounds: int,
    delta_y: float,
    x: float,
    y: float,
    input_mode: str,
    gap_min: float,
    gap_max: float,
) -> None:
    """在当前在售列表中做真人滚动，并由后台监听响应体。"""
    proc = None
    client = None
    monitor_task = None
    stop_event = asyncio.Event()
    try:
        proc = await _start_wmpf_bridge()
        async with websockets.connect(CDP_PROXY_URL) as socket:
            client = _CdpClient(socket)
            await client.start()
            monitor = _NetworkMonitor(client, stop_event)
            monitor_task = asyncio.create_task(monitor.run())
            log.info(
                "滚动 MVP 已连接 CDP 桥：请打开行舟深房小程序并进入在售房源列表，"
                "然后回到终端按回车开始滚动"
            )
            await asyncio.to_thread(input)
            await _enable_network_after_manual_ready(client)
            log.info(
                "开始真人滚动；已捕获在售响应=%d",
                monitor.response_count(constants.API_SALES_PATH),
            )
            for index in range(1, rounds + 1):
                if input_mode == "touch":
                    ok = await _human_swipe(client, x, y, delta_y)
                else:
                    response = await client.command(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mouseWheel",
                            "x": x,
                            "y": y,
                            "deltaX": 0,
                            "deltaY": delta_y * random.uniform(0.78, 1.12),
                            "modifiers": 0,
                            "pointerType": "mouse",
                        },
                    )
                    ok = response is not None and "error" not in response
                if not ok:
                    raise RuntimeError(f"滚动第{index}/{rounds}次未收到完整 CDP 响应")
                log.info(
                    "滚动第%d/%d已发送%s手势；当前在售响应=%d",
                    index,
                    rounds,
                    "触摸" if input_mode == "touch" else "鼠标",
                    monitor.response_count(constants.API_SALES_PATH),
                )
                await asyncio.sleep(random.uniform(gap_min, gap_max))
            log.info("滚动已完成，Network事件=%s；回到终端按回车关闭桥", monitor.event_counts)
            await asyncio.to_thread(input)
    except (OSError, RuntimeError, websockets.WebSocketException) as exc:
        log.exception(
            "滚动 MVP 失败: %s (%s)",
            repr(exc),
            type(exc).__name__,
        )
    finally:
        stop_event.set()
        if monitor_task is not None and not monitor_task.done():
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if client is not None:
            await client.close()
        await _stop_wmpf_bridge(proc)
        log.info("滚动 MVP 已结束，WMPF 调试桥已关闭")


def _is_xzsfbj_request(headers: dict) -> bool:
    """只接收行舟深房域名的请求头，避免误取其他小程序 token。"""
    host = str(headers.get(":authority") or headers.get("Host") or headers.get("host") or "")
    host = host.split(":", maxsplit=1)[0].lower()
    return host == "xzsfbj.com.cn" or host.endswith(".xzsfbj.com.cn")


async def _read_captured_token(socket, timeout: float = TOKEN_CAPTURE_TIMEOUT) -> str | None:
    """从 WMPF CDP 网络事件读取 Authorization，不写入磁盘。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while remaining := deadline - asyncio.get_running_loop().time():
        try:
            raw_message = await asyncio.wait_for(socket.recv(), timeout=remaining)
        except TimeoutError:
            break
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            continue
        if message.get("method") != "Network.requestWillBeSentExtraInfo":
            continue
        headers = message.get("params", {}).get("headers", {})
        if not _is_xzsfbj_request(headers):
            continue
        token = next(
            (value for key, value in headers.items() if key.lower() == "authorization"),
            "",
        )
        if token:
            return str(token)
    return None


def _token_capture_prompt(reason: str = "") -> str:
    """生成 token 捕获期间的人工操作提示。"""
    if reason:
        return (
            f"\n⚠ {reason}"
            "\n请从微信会话重新打开「行舟深房」小程序，再点进任意小区的成交记录刷新 token。"
            "\n页面正常出数后，脚本会自动继续，无需按回车。\n"
        )
    return (
        "\n请从微信会话重新打开「行舟深房」小程序，并点进任意小区的成交记录。"
        "\n页面正常出数后，脚本会自动继续，无需按回车。\n"
    )


async def ensure_token(reason: str = "") -> str | None:
    """通过 WMPF CDP 网络事件获取 token。"""
    proc = None
    try:
        proc = await _start_wmpf_bridge()
        async with websockets.connect(CDP_PROXY_URL) as socket:
            await socket.send(json.dumps({"id": 1, "method": "Network.enable"}))
            log.info(_token_capture_prompt(reason))
            token = await _read_captured_token(socket)
        if not token:
            log.error("未捕获到 token，请确认已从微信会话重新打开小程序并点进成交记录")
            return None
        masked = f"{token[:8]}...{token[-8:]}" if len(token) > 20 else "***"
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        log.info("token 已捕获: %s（长度=%d，指纹=%s）", masked, len(token), fingerprint)
        return token
    except (OSError, RuntimeError, websockets.WebSocketException) as exc:
        log.error("token 抓取失败: %s", exc)
        return None
    finally:
        await _stop_wmpf_bridge(proc)
        log.info("WMPF 调试桥已关闭")


# ============================================================
# 数据层
# ============================================================

class LoginExpired(Exception):
    """token 登录态失效（errCode=40001/41000），对应 LOGIN_EXPIRED。"""


class Blocked(Exception):
    """被风控/限制，对应 WAIT_MANUAL_VERIFY。"""


class ApiResponseError(RuntimeError):
    """接口返回了未识别的业务异常。"""


_RISK_KEYWORDS = ("风控", "限制", "封", "禁止", "block", "risk", "deny", "forbidden", "频繁")
_SENSITIVE_DEBUG_KEYS = ("authorization", "token", "cookie", "password")


def _sanitize_debug_payload(value):
    if isinstance(value, dict):
        return {
            key: "***" if any(marker in key.lower() for marker in _SENSITIVE_DEBUG_KEYS)
            else _sanitize_debug_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_debug_payload(item) for item in value]
    return value


def _dump_api_payload(name: str, payload) -> None:
    """仅在 --debug 下保存脱敏接口响应，供后续 parser 核对。"""
    if not is_debug_mode():
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    output = DEBUG_DIR / f"xzsfbj_{name}.json"
    with open(output, "w", encoding="utf-8") as file:
        json.dump(_sanitize_debug_payload(payload), file, ensure_ascii=False, indent=2)
    log.info("已导出接口调试响应: %s", output)


def _check_err(api_name, data):
    if not isinstance(data, dict):
        raise ApiResponseError(f"{api_name} 响应不是 JSON 对象")
    err = data.get("errCode")
    if str(err) == "0":
        return
    msg = str(data.get("errMsg", ""))
    if str(err) in constants.DEAL_PAGE_RISK_CODES and api_name == "getXqDeal":
        raise DealPageRisk(f"{api_name} 平台风控 errCode={err} msg={msg}")
    if any(kw in msg.lower() for kw in _RISK_KEYWORDS):
        raise Blocked(f"errCode={err} msg={msg}")
    if str(err) in {"40001", "41000"}:
        raise LoginExpired(f"errCode={err} msg={msg}")
    raise ApiResponseError(f"{api_name} 异常 errCode={err} msg={msg}")


def aes_encrypt_region_id(region_id):
    from Crypto.Cipher import AES
    data = str(region_id).encode("utf-8")
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len]) * pad_len
    return base64.b64encode(
        AES.new(constants.get_aes_key(), AES.MODE_ECB).encrypt(padded)
    ).decode()


def load_community_list():
    if not XQ_DATA_FILE.exists():
        log.error(
            "小区清单不存在：%s。请先打开一次行舟深房小程序，或设置 %s",
            XQ_DATA_FILE,
            XQ_DATA_PATH_ENV,
        )
        sys.exit(1)
    with open(XQ_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def match_community(communities, name, administrative_district=None):
    """按正式 parser 的保守规则匹配，行政区仅用于同名小区消歧。"""
    candidates = find_community_candidates(
        parse_community_index(communities), name, administrative_district
    )
    return candidates[0] if len(candidates) == 1 else None


def make_headers(token):
    return {
        "Authorization": token,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Referer": REFERER,
    }


async def _human_api_pause(min_gap: float, max_gap: float) -> None:
    """按平台接口节奏等待，避免阻塞异步采集流程。"""
    await asyncio.sleep(random.uniform(min_gap, max_gap))


# ============================================================
# 采集（httpx 同步请求，风控/失效时抛异常交由上层 async 处理）
# ============================================================

def fetch_deals(client, headers, region_id, page=1, acreage=""):
    enc = aes_encrypt_region_id(region_id)
    params = {"regionId": enc, "page": str(page), "roomCount": "",
              "acreage": acreage, "year": ""}
    response = client.get(f"{BASE_URL}/api/house/getXqDeal", params=params, headers=headers)
    response.raise_for_status()
    data = response.json()
    _dump_api_payload(f"deals_page_{page}", data)
    _check_err("getXqDeal", data)
    return data.get("data")


def fetch_sales(client, headers, region_id):
    params = {"regionId": str(region_id)}
    response = client.get(f"{BASE_URL}/api/house/getCommunitySales",
                          params=params, headers=headers)
    response.raise_for_status()
    data = response.json()
    _dump_api_payload("sales", data)
    _check_err("getCommunitySales", data)
    return data.get("data")


# ============================================================
# 单小区采集（构造 PlatformResult，复用 prepare_listing_data）
# ============================================================

async def collect_one(client, headers, community, area, started_at, request_id=None):
    """采集单个小区，并将接口异常映射为统一平台结果。"""
    try:
        return await _do_collect_one(client, headers, community, area, started_at, request_id)
    except LoginExpired as exc:
        log.warning("行舟深房登录已失效: %s", exc)
        return short_circuit_result(
            "行舟深房", PlatformResultStatus.LOGIN_EXPIRED, str(exc), request_id, started_at
        )
    except Blocked as exc:
        log.warning("行舟深房命中风控: %s", exc)
        return short_circuit_result(
            "行舟深房", PlatformResultStatus.WAIT_MANUAL_VERIFY, str(exc), request_id, started_at
        )
    except (httpx.HTTPError, ApiResponseError, ValueError, TypeError, KeyError) as exc:
        log.exception("行舟深房采集异常")
        return short_circuit_result(
            "行舟深房", PlatformResultStatus.ERROR, str(exc), request_id, started_at
        )


async def _do_collect_one(client, headers, community, area, started_at, request_id=None):
    """执行行舟深房接口采集主链路。"""
    region_id = community["regionId"]
    cname = community["name"]
    log.info("    匹配结果: %s | regionId=%s | %s %s",
             cname, region_id, community.get("area", ""), community.get("district", ""))

    all_deals = []
    # 成交记录（自动翻页）
    page = 1
    deal_total = 0
    deal_incomplete_reason = None
    while True:
        log.info("    成交记录 第%d页...", page)
        risk_retries = 0
        while True:
            try:
                deal_data = await asyncio.to_thread(
                    fetch_deals, client, headers, region_id, page, area
                )
                break
            except DealPageRisk as exc:
                if risk_retries < constants.DEAL_PAGE_RISK_MAX_RETRIES:
                    risk_retries += 1
                    log.warning(
                        "    成交第%d页触发平台风控，%s秒后重试（第%d/%d次）: %s",
                        page,
                        constants.DEAL_PAGE_RISK_RETRY_DELAY,
                        risk_retries,
                        constants.DEAL_PAGE_RISK_MAX_RETRIES,
                        exc,
                    )
                    await _human_api_pause(
                        constants.DEAL_PAGE_RISK_RETRY_DELAY,
                        constants.DEAL_PAGE_RISK_RETRY_DELAY,
                    )
                    continue
                deal_incomplete_reason = (
                    f"后续成交记录因平台风控未能获取（第{page}页，"
                    f"已重试{constants.DEAL_PAGE_RISK_MAX_RETRIES}次），"
                    f"已保留此前成功获取的{len(all_deals)}条成交数据"
                )
                log.warning("    %s", deal_incomplete_reason)
                break
        if deal_incomplete_reason:
            break
        if deal_data is None:
            break
        if not isinstance(deal_data, dict):
            raise ApiResponseError("getXqDeal 的 data 不是对象")
        deal_total = int(deal_data.get("count", deal_total) or 0)
        page_deals = deal_data.get("dealList", []) or []
        if not isinstance(page_deals, list):
            raise ApiResponseError("getXqDeal 的 dealList 不是列表")
        all_deals.extend(page_deals)
        log.info("    本页 %d 条，累计 %d/%d", len(page_deals), len(all_deals), deal_total)
        if len(page_deals) < 30 or len(all_deals) >= deal_total:
            log.info("    成交记录已全部取完")
            break
        page += 1
        await _human_api_pause(PAGE_GAP_MIN, PAGE_GAP_MAX)

    # 在售房源
    await _human_api_pause(STEP_GAP_MIN, STEP_GAP_MAX)
    log.info("    在售房源...")
    sales = await asyncio.to_thread(fetch_sales, client, headers, region_id) or []
    if not isinstance(sales, list):
        raise ApiResponseError("getCommunitySales 的 data 不是列表")

    # 用 prepare_listing_data 过滤（复用 base.py，不自己造过滤逻辑）
    raw_snapshots = []
    for s in sales:
        try:
            unit_yuan = int(float(s.get("unitPrice", 0)) * 10000)
        except (ValueError, TypeError):
            continue
        try:
            area_val = float(s.get("acreage", 0))
        except (ValueError, TypeError):
            area_val = None
        try:
            total_price = float(s.get("price", 0))
        except (ValueError, TypeError):
            total_price = None
        raw_snapshots.append(ListingSnapshot(
            house_id=str(s.get("id", "")),
            community_name=cname,
            unit_price=unit_yuan,
            total_price=total_price,
            area=area_val,
            layout=s.get("layout", ""),
        ))

    filtered_snapshots, quote_prices, reference = prepare_listing_data_with_reference(
        raw_snapshots, cname, area
    )
    if not filtered_snapshots:
        return short_circuit_result(
            "行舟深房",
            listing_no_data_status(raw_snapshots, cname, area),
            listing_no_data_reason(raw_snapshots, cname, area),
            request_id,
            started_at,
        )

    # 成交接口已按 acreage 请求，但仍复用正式 parser 做面积 + 近半年本地复核，
    # 避免 MVP 与正式 adapter 的成交口径分叉。
    deal_prices, deal_records = filter_deal_records(
        all_deals,
        area,
        DEAL_AREA_TOLERANCE,
        months=DEAL_LOOKBACK_MONTHS,
    )
    log.info(
        "成交本地复核: 原始 %d 条 -> 请求面积 %.1f㎡ ±%.1f㎡ 且近%d个月命中 %d 条",
        len(all_deals),
        area,
        DEAL_AREA_TOLERANCE,
        DEAL_LOOKBACK_MONTHS,
        len(deal_records),
    )

    return PlatformResult(
        name="行舟深房",
        status=PlatformResultStatus.SUCCESS,
        quote_prices=quote_prices,
        deal_prices=deal_prices,
        deal_records=deal_records,
        listing_snapshots=filtered_snapshots,
        deal_source="成交记录" if deal_prices else "无",
        reason=deal_incomplete_reason,
        request_id=request_id,
        elapsed_seconds=round(time.time() - started_at, 2),
        **reference,
    )


# ============================================================
# token 刷新策略
# ============================================================

def need_refresh(count_since_refresh, last_refresh_time, max_communities, refresh_interval):
    if count_since_refresh >= max_communities:
        return True, f"已达数量上限 {max_communities} 个小区"
    if time.time() - last_refresh_time >= refresh_interval:
        return True, f"已达刷新间隔 {refresh_interval} 秒"
    return False, ""


# ============================================================
# 主流程
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="行舟深房（xzsfbj）采集 MVP",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--community",
                        help="小区名称，多个用逗号分隔")
    parser.add_argument("--area", type=float, help="面积（㎡，如 91.5）")
    parser.add_argument(
        "--scroll-mvp",
        action="store_true",
        help="只模拟小程序在售列表滚动，不捕获 token、不调用业务接口",
    )
    parser.add_argument(
        "--probe-mvp",
        action="store_true",
        help="Probe page text and Network only; do not capture token or call business APIs",
    )
    parser.add_argument(
        "--ui-mvp",
        action="store_true",
        help="通过小程序 UI 输入、点击、真人滚动并从后台 Network 采集在售数据",
    )
    parser.add_argument(
        "--ui-hold-seconds",
        type=float,
        default=0.0,
        help="UI MVP 完成后额外保持桥接的秒数（默认不额外等待）",
    )
    parser.add_argument(
        "--ui-scroll-rounds",
        type=int,
        default=40,
        help="UI MVP 真人滚动安全上限（默认 40 次，不代表页面超时）",
    )
    parser.add_argument(
        "--ui-input",
        choices=("mouse", "touch"),
        default="mouse",
        help="UI 点击输入模型：mouse 更接近桌面微信/浏览器，touch 用触摸事件（默认 mouse）",
    )
    parser.add_argument(
        "--probe-seconds",
        type=float,
        default=20.0,
        help="Probe Network listening duration in seconds",
    )
    parser.add_argument("--scroll-rounds", type=int, default=10,
                        help="滚动轮数（默认 10）")
    parser.add_argument("--scroll-delta", type=float, default=520.0,
                        help="每轮滚动基础像素（默认 520）")
    parser.add_argument("--scroll-input", choices=("touch", "wheel"), default="touch",
                        help="输入类型：touch 或 wheel（默认 touch）")
    parser.add_argument("--scroll-x", type=float, default=200.0,
                        help="滚动事件横坐标（默认 200）")
    parser.add_argument("--scroll-y", type=float, default=520.0,
                        help="滚动事件中心纵坐标（默认 520）")
    parser.add_argument(
        "--administrative-district",
        help="行政区（用于同名小区消歧，如 南山区）；UI MVP 必填",
    )
    parser.add_argument("--max-communities", type=int, default=MAX_COMMUNITIES_PER_TOKEN,
                        help=f"单 token 最大采集小区数（默认 {MAX_COMMUNITIES_PER_TOKEN}）")
    parser.add_argument("--refresh-interval", type=int, default=REFRESH_INTERVAL,
                        help=f"token 刷新间隔秒数（默认 {REFRESH_INTERVAL}）")
    parser.add_argument("--debug", "--excel", dest="debug", action="store_true",
                        help="导出脱敏接口 JSON、UI DOM HTML/快照和 Network 响应")
    args = parser.parse_args()

    set_debug_mode(args.debug)
    if args.ui_mvp:
        if not args.community or args.area is None:
            parser.error("--ui-mvp requires --community and --area")
        if not args.administrative_district or not args.administrative_district.strip():
            parser.error(
                "--ui-mvp requires --administrative-district，"
                "用于同名小区消歧"
            )
        if args.ui_hold_seconds < 0:
            parser.error("--ui-hold-seconds must be non-negative")
        if args.ui_scroll_rounds <= 0:
            parser.error("--ui-scroll-rounds must be greater than 0")
        await run_ui_mvp(
            args.community.strip(),
            args.area,
            args.ui_hold_seconds,
            args.ui_scroll_rounds,
            args.ui_input,
            args.administrative_district.strip(),
        )
        return
    if args.probe_mvp:
        if args.probe_seconds <= 0:
            parser.error("--probe-seconds must be greater than 0")
        await run_probe_mvp(args.probe_seconds)
        return
    if args.scroll_mvp:
        if args.scroll_rounds <= 0 or args.scroll_delta <= 0:
            parser.error("--scroll-rounds 和 --scroll-delta 必须大于 0")
        await run_scroll_mvp(
            rounds=args.scroll_rounds,
            delta_y=args.scroll_delta,
            x=args.scroll_x,
            y=args.scroll_y,
            input_mode=args.scroll_input,
            gap_min=1.0,
            gap_max=2.0,
        )
        return
    if not args.community or args.area is None:
        parser.error("普通采集模式必须同时指定 --community 和 --area")
    community_names = [c.strip() for c in args.community.split(",") if c.strip()]
    area = args.area
    log.info("待采集小区: %s（共 %d 个）", community_names, len(community_names))

    # ---- [1] 获取 token ----
    log.info("[1] 获取 token")
    token = await ensure_token()
    if not token:
        log.error("未能获取 token，退出")
        return
    headers = make_headers(token)
    last_refresh_time = time.time()
    count_since_refresh = 0

    # ---- [2] 匹配小区 ----
    log.info("[2] 匹配小区")
    community_list = load_community_list()
    targets = []
    for name in community_names:
        community = match_community(
            community_list, name, args.administrative_district
        )
        if not community:
            district_hint = (
                f"（行政区：{args.administrative_district}）"
                if args.administrative_district else ""
            )
            log.warning("    未找到唯一小区：%s%s，跳过", name, district_hint)
            continue
        targets.append(community)
        log.info("    匹配: %s | regionId=%s", community["name"], community["regionId"])
    if not targets:
        log.error("没有匹配到任何小区，退出")
        return

    # ---- [3] 逐个采集 ----
    log.info("[3] 开始采集")
    completed_count = 0
    with httpx.Client(trust_env=False, timeout=15) as client:
        for i, community in enumerate(targets, 1):
            log.info("-" * 60)
            log.info("[%d/%d] %s", i, len(targets), community["name"])

            # token 主动刷新判定
            need, reason = need_refresh(
                count_since_refresh, last_refresh_time,
                args.max_communities, args.refresh_interval,
            )
            if need and i > 1:
                log.info("[刷新] %s", reason)
                token = await ensure_token(reason=reason)
                if not token:
                    log.error("刷新 token 失败，停止采集")
                    break
                headers = make_headers(token)
                last_refresh_time = time.time()
                count_since_refresh = 0

            started_at = time.time()
            result = await collect_one(client, headers, community, area, started_at, f"mvp-{i}")

            # 用 print_mvp_result 输出（复用现有 MVP 输出模块）
            _print_result(community["name"], area, result)
            completed_count += 1
            if result.status in {
                PlatformResultStatus.LOGIN_EXPIRED,
                PlatformResultStatus.WAIT_MANUAL_VERIFY,
            }:
                log.warning("当前 token 不可继续使用，停止本批次，等待人工重新运行 MVP")
                break
            count_since_refresh += 1

            if i < len(targets):
                await _human_api_pause(STEP_GAP_MIN, STEP_GAP_MAX)

    log.info("=" * 60)
    log.info("本批次处理完成: %d/%d 个小区", completed_count, len(targets))


def _print_result(community_name, area, result):
    """用 print_mvp_result 统一输出（复用现有 MVP 输出模块）。"""
    if result.reason:
        log.warning("MVP 结果说明：%s", result.reason)
    if result.status == PlatformResultStatus.SUCCESS:
        evaluation = evaluate_algorithm(
            inputs=AlgorithmInput(
                quote_price_lists=[result.quote_prices],
                weighted_median_discount=config.get_weighted_median_discount(),
                deal_price_lists=[result.deal_prices] if result.deal_source == "成交记录" else [],
            ),
        )
        quote_avg = round_price(evaluation.quote_avg)
        deal_avg = round_price(evaluation.deal_avg)
        final_price = round_price(evaluation.decision.final_price)
        branch = evaluation.decision.branch
    else:
        quote_avg = None
        deal_avg = None
        final_price = None
        branch = PLATFORM_RESULT_STATUS_TEXT.get(result.status, str(result.status))

    print_mvp_result(
        platform="行舟深房",
        community_name=community_name,
        area=area,
        trace={
            "home_blocked": False,
            "search_url": "",
            "area_ok": result.status == PlatformResultStatus.SUCCESS,
            "area_url": "",
            "area_pages": 0,
            "detail_ok": False,
            "detail_url": "",
        },
        listings={
            # NO_MATCHING_AREA 时 quote_prices 为空，但 listing_snapshots 保留
            # Network 实际采集到的原始房源，便于 MVP 核对最后一条数据。
            "count": len(result.listing_snapshots),
            "avg": quote_avg,
            "snapshots": result.listing_snapshots,
        },
        deals={
            "count": len(result.deal_prices),
            "avg": deal_avg,
            "records": result.deal_records,
            "substitute": "" if result.deal_prices else result.deal_source,
        },
        result={
            "quote_avg": quote_avg,
            "deal_avg": deal_avg,
            "final_price": final_price,
            "branch": branch,
        },
        elapsed=result.elapsed_seconds or 0.0,
    )


if __name__ == "__main__":
    asyncio.run(main())
