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
import shutil
import subprocess
import sys
import time
from pathlib import Path

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
from app.parsers.xzsfbj import (
    filter_deal_records,
    find_community_candidates,
    parse_community_index,
)
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
TOKEN_CAPTURE_TIMEOUT = 180
XQ_DATA_PATH_ENV = "XZSFBJ_XQ_DATA_PATH"
MINI_PROGRAM_APP_ID = "wxd49effb77288061d"

# 防风控参数（行舟深房接口实测需要 5~8 秒真人间隔）
PAGE_GAP_MIN = 5.0
PAGE_GAP_MAX = 8.0
STEP_GAP_MIN = 5.0
STEP_GAP_MAX = 8.0
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


async def _start_wmpf_bridge():
    """启动并确认 WMPF 调试桥已完成 Frida 注入。"""
    log.info("启动 WMPF 调试桥（不修改系统代理）...")
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
    while remaining := deadline - asyncio.get_running_loop().time():
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
    detail = " | ".join(lines[-3:]) or f"退出码={proc.returncode}"
    raise RuntimeError(f"WMPF 调试桥启动失败: {detail}")


async def _stop_wmpf_bridge(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        await asyncio.to_thread(proc.wait, 5)
    except subprocess.TimeoutExpired:
        proc.kill()
        await asyncio.to_thread(proc.wait, 5)


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
        async with websockets.connect("ws://127.0.0.1:62000") as socket:
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
    while True:
        log.info("    成交记录 第%d页...", page)
        deal_data = await asyncio.to_thread(
            fetch_deals, client, headers, region_id, page, area
        )
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
    parser.add_argument("--community", required=True,
                        help="小区名称，多个用逗号分隔")
    parser.add_argument("--area", type=float, required=True, help="面积（㎡，如 91.5）")
    parser.add_argument(
        "--administrative-district",
        help="行政区（用于同名小区消歧，如 南山区）；不填则要求名称唯一",
    )
    parser.add_argument("--max-communities", type=int, default=MAX_COMMUNITIES_PER_TOKEN,
                        help=f"单 token 最大采集小区数（默认 {MAX_COMMUNITIES_PER_TOKEN}）")
    parser.add_argument("--refresh-interval", type=int, default=REFRESH_INTERVAL,
                        help=f"token 刷新间隔秒数（默认 {REFRESH_INTERVAL}）")
    parser.add_argument("--debug", "--excel", dest="debug", action="store_true",
                        help="导出脱敏接口 JSON，供核对响应字段")
    args = parser.parse_args()

    set_debug_mode(args.debug)
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
            "area_ok": True,
            "area_url": "",
            "area_pages": 0,
            "detail_ok": False,
            "detail_url": "",
        },
        listings={
            "count": len(result.quote_prices),
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
