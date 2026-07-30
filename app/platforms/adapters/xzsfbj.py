# -*- coding: utf-8 -*-
"""行舟深房 WMPF + HTTP 接口采集逻辑。

小程序接口没有可复用的网页 DOM，因此本 adapter 只复用基类的统一结果、
面积/小区过滤和人工风控协调能力；响应字段转换全部委托给 parser。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import websockets

from app.core import config
from app.core.models import InquiryRequest, PlatformResult
from app.core.status import PlatformHealthStatus, PlatformResultStatus
from app.parsers import xzsfbj as parsers
from app.platforms import xzsfbj_constants as constants
from app.platforms.base import (
    listing_no_data_reason,
    listing_no_data_status,
    notify_manual_verify_state,
    prepare_listing_data_with_reference,
    short_circuit_result,
    wait_for_manual_unblock,
)
from app.utils.debug_utils import is_debug_mode
from app.utils.listing_dedup import deduplicate_same_platform

log = logging.getLogger(__name__)


class LoginExpired(Exception):
    """接口登录态失效。"""


class Blocked(Exception):
    """接口返回平台风控或访问限制。"""


class ApiResponseError(RuntimeError):
    """接口返回未识别的业务错误。"""


def _sanitize_debug_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***"
            if any(marker in key.lower() for marker in constants.SENSITIVE_DEBUG_KEYS)
            else _sanitize_debug_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_debug_payload(item) for item in value]
    return value


def _dump_api_payload(name: str, payload: Any) -> None:
    """仅在调试开关打开时导出脱敏响应，默认不落盘。"""
    if not is_debug_mode():
        return
    output_dir = config.DEBUG_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"xzsfbj_{name}.json"
    output.write_text(
        json.dumps(_sanitize_debug_payload(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("已导出接口调试响应: %s", output)


def encrypt_region_id(region_id: Any) -> str:
    """按小程序约定 AES-ECB 加密 regionId。"""
    from Crypto.Cipher import AES

    raw = str(region_id).encode("utf-8")
    padding = 16 - len(raw) % 16
    padded = raw + bytes([padding]) * padding
    encrypted = AES.new(constants.get_aes_key(), AES.MODE_ECB).encrypt(padded)
    return base64.b64encode(encrypted).decode("ascii")


def _is_xzsfbj_request(headers: dict[str, Any]) -> bool:
    host = str(
        headers.get(":authority") or headers.get("Host") or headers.get("host") or ""
    ).split(":", 1)[0].lower()
    return host == "xzsfbj.com.cn" or host.endswith(".xzsfbj.com.cn")


def _check_api_error(api_name: str, payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ApiResponseError(f"{api_name} 响应不是 JSON 对象")
    error_code = str(payload.get("errCode", ""))
    if error_code == "0":
        return
    message = str(payload.get("errMsg", ""))
    lowered = message.casefold()
    if any(keyword.casefold() in lowered for keyword in constants.RISK_KEYWORDS):
        raise Blocked(f"errCode={error_code} msg={message}")
    if error_code in constants.LOGIN_ERROR_CODES:
        raise LoginExpired(f"errCode={error_code} msg={message}")
    raise ApiResponseError(f"{api_name} 异常 errCode={error_code} msg={message}")


class XzsfbjApiAdapter:
    """行舟深房接口采集器，供平台薄壳委托。"""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_acquired_at = 0.0
        self._communities_since_refresh = 0
        self._token_lock = asyncio.Lock()
        self._last_collect_finished_at = 0.0

    @property
    def xq_data_file(self) -> Path:
        return constants.resolve_xq_data_file()

    def dependencies_ready(self) -> tuple[bool, str]:
        """检查本地索引、Node 和桥依赖，不启动桥接。"""
        if not self.xq_data_file.is_file():
            return False, f"小区索引不存在: {self.xq_data_file}；请先打开行舟深房小程序"
        node = shutil.which("node")
        if node is None:
            return False, "未找到 Node.js，无法启动 WMPF 调试桥"
        bridge_dir = constants.resolve_wmpf_bridge_dir()
        if not (bridge_dir / "src" / "index.js").is_file():
            return False, f"未找到 WMPF 调试桥: {bridge_dir}"
        if not (bridge_dir / "node_modules" / "frida").is_dir():
            return False, "WMPF 调试桥依赖未安装，请运行 scripts/setup_xzsfbj_wmpf_bridge.ps1"
        return True, "READY"

    async def _start_bridge(self):
        ready, reason = self.dependencies_ready()
        if not ready:
            raise RuntimeError(reason)
        bridge_dir = constants.resolve_wmpf_bridge_dir()
        node = shutil.which("node")
        assert node is not None
        log.info("启动 WMPF 调试桥（不修改系统代理）...")
        proc = subprocess.Popen(
            [node, "src/index.js"],
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
                    asyncio.to_thread(proc.stdout.readline), timeout=remaining
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
        await self._stop_bridge(proc)
        detail = " | ".join(lines[-3:]) or f"退出码={proc.returncode}"
        raise RuntimeError(f"WMPF 调试桥启动失败: {detail}")

    @staticmethod
    async def _stop_bridge(proc) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            await asyncio.to_thread(proc.wait, 5)
        except subprocess.TimeoutExpired:
            proc.kill()
            await asyncio.to_thread(proc.wait, 5)

    async def _capture_token(self, reason: str = "") -> Optional[str]:
        proc = None
        try:
            proc = await self._start_bridge()
            async with websockets.connect(constants.WMPF_WS_URL) as socket:
                await socket.send(json.dumps({"id": 1, "method": "Network.enable"}))
                if reason:
                    log.info(
                        "\n⚠ %s\n请从微信会话重新打开「行舟深房」并进入成交记录，"
                        "页面正常出数后脚本自动继续。\n", reason
                    )
                else:
                    log.info(
                        "\n请从微信会话重新打开「行舟深房」小程序，并进入任意小区的成交记录。"
                        "\n页面正常出数后脚本自动继续，无需按回车。\n"
                    )
                deadline = asyncio.get_running_loop().time() + constants.TOKEN_CAPTURE_TIMEOUT
                token = None
                while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
                    except TimeoutError:
                        break
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if message.get("method") != "Network.requestWillBeSentExtraInfo":
                        continue
                    headers = message.get("params", {}).get("headers", {})
                    if not _is_xzsfbj_request(headers):
                        continue
                    token = next(
                        (
                            str(value)
                            for key, value in headers.items()
                            if key.lower() == "authorization" and value
                        ),
                        None,
                    )
                    if token:
                        break
            if not token:
                log.error("未捕获到 token，请确认小程序已重新打开并进入成交记录")
                return None
            masked = f"{token[:8]}...{token[-8:]}" if len(token) > 20 else "***"
            fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
            log.info("token 已捕获: %s（长度=%d，指纹=%s）", masked, len(token), fingerprint)
            return token
        except (OSError, RuntimeError, websockets.WebSocketException) as exc:
            log.error("token 抓取失败: %s", exc)
            return None
        finally:
            await self._stop_bridge(proc)
            if proc is not None:
                log.info("WMPF 调试桥已关闭")

    async def _get_token(self, *, force: bool = False, reason: str = "") -> Optional[str]:
        now = time.time()
        if (
            not force
            and self._token
            and self._communities_since_refresh < constants.MAX_COMMUNITIES_PER_TOKEN
            and now - self._token_acquired_at < constants.TOKEN_REFRESH_INTERVAL
        ):
            return self._token
        async with self._token_lock:
            now = time.time()
            if (
                not force
                and self._token
                and self._communities_since_refresh < constants.MAX_COMMUNITIES_PER_TOKEN
                and now - self._token_acquired_at < constants.TOKEN_REFRESH_INTERVAL
            ):
                return self._token
            token = await self._capture_token(reason)
            if token:
                self._token = token
                self._token_acquired_at = time.time()
                self._communities_since_refresh = 0
            return token

    async def _wait_between_communities(self) -> None:
        """控制不同小区接口批次之间的真人节奏。"""
        if not self._last_collect_finished_at:
            return
        target_gap = random.uniform(constants.COMMUNITY_GAP_MIN, constants.COMMUNITY_GAP_MAX)
        remaining = target_gap - (time.monotonic() - self._last_collect_finished_at)
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _load_communities(self) -> list[dict[str, Any]]:
        path = self.xq_data_file
        if not path.is_file():
            raise FileNotFoundError(f"小区索引不存在: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiResponseError(f"读取 xqData.json 失败: {path}: {exc}") from exc
        return parsers.parse_community_index(payload)

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": token,
            "User-Agent": constants.USER_AGENT,
            "Content-Type": "application/json",
            "Referer": constants.REFERER,
        }

    async def _request_json(
        self, client: httpx.Client, path: str, params: dict[str, str],
        headers: dict[str, str], label: str,
    ) -> dict[str, Any]:
        response = await asyncio.to_thread(
            client.get,
            f"{constants.BASE_URL}{path}",
            params=params,
            headers=headers,
        )
        if response.status_code == 401:
            raise LoginExpired(f"{label} HTTP 401")
        if response.status_code == 403:
            raise Blocked(f"{label} HTTP 403")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiResponseError(f"{label} 响应不是 JSON") from exc
        _dump_api_payload(label, payload)
        _check_api_error(label, payload)
        return payload

    async def _fetch_deals(
        self, client: httpx.Client, headers: dict[str, str],
        region_id: Any, page: int, area: float,
    ) -> tuple[int, list[dict[str, Any]]]:
        payload = await self._request_json(
            client,
            constants.API_DEALS_PATH,
            {
                "regionId": encrypt_region_id(region_id),
                "page": str(page),
                "roomCount": "",
                "acreage": str(area),
                "year": "",
            },
            headers,
            f"deals_page_{page}",
        )
        return parsers.parse_deal_page(payload.get("data"))

    async def _fetch_sales(
        self, client: httpx.Client, headers: dict[str, str], region_id: Any,
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            client,
            constants.API_SALES_PATH,
            {"regionId": str(region_id)},
            headers,
            "sales",
        )
        return parsers.parse_sales(payload.get("data"))

    async def _wait_api_risk(self, reason: str, request_id: Optional[str]) -> bool:
        context = "行舟深房(xzsfbj)/接口"
        await notify_manual_verify_state(
            context, PlatformHealthStatus.WAIT_MANUAL_VERIFY, reason
        )
        try:
            await wait_for_manual_unblock(context, attempt=1)
        except Exception as exc:
            log.warning("行舟深房人工风控确认失败 request=%s: %s", request_id, exc)
            return False
        self._token = None
        token = await self._get_token(force=True, reason="人工确认完成，请重新捕获 token")
        if not token:
            return False
        await notify_manual_verify_state(context, PlatformHealthStatus.READY, "接口风控已人工确认")
        return True

    async def collect_one(
        self,
        client: httpx.Client,
        community: dict[str, Any],
        area: float,
        token: str,
        request_id: Optional[str],
        started_at: float,
        *,
        allow_manual_retry: bool = True,
    ) -> PlatformResult:
        headers = self._headers(token)
        try:
            region_id = community.get("regionId")
            if region_id in (None, ""):
                raise ApiResponseError("匹配小区缺少 regionId")
            community_name = str(community.get("name") or "")
            log.info(
                "匹配结果: %s | regionId=%s | %s %s",
                community_name,
                region_id,
                community.get("area", ""),
                community.get("district", ""),
            )

            all_deals: list[dict[str, Any]] = []
            page = 1
            deal_total = 0
            while True:
                log.info("成交记录 第%d页...", page)
                deal_total, page_deals = await self._fetch_deals(
                    client, headers, region_id, page, area
                )
                all_deals.extend(page_deals)
                log.info(
                    "本页 %d 条，累计 %d/%d", len(page_deals), len(all_deals), deal_total
                )
                if len(page_deals) < constants.DEAL_PAGE_SIZE or len(all_deals) >= deal_total:
                    log.info("成交记录已全部取完")
                    break
                page += 1
                await asyncio.sleep(random.uniform(constants.PAGE_GAP_MIN, constants.PAGE_GAP_MAX))

            await asyncio.sleep(
                random.uniform(constants.COMMUNITY_GAP_MIN, constants.COMMUNITY_GAP_MAX)
            )
            log.info("在售房源...")
            sales = await self._fetch_sales(client, headers, region_id)
            raw_snapshots = parsers.parse_listing_snapshots(sales, community_name)
            filtered_snapshots, quote_prices, reference = prepare_listing_data_with_reference(
                raw_snapshots, community_name, area
            )
            if not filtered_snapshots:
                return short_circuit_result(
                    "行舟深房",
                    listing_no_data_status(raw_snapshots, community_name, area),
                    listing_no_data_reason(raw_snapshots, community_name, area),
                    request_id,
                    started_at,
                )

            deal_prices, deal_records = parsers.filter_deal_records(
                all_deals,
                area,
                constants.DEAL_AREA_TOLERANCE,
                months=constants.DEAL_LOOKBACK_MONTHS,
            )
            deal_record_dicts = [
                {
                    "area": record.area,
                    "date": record.date,
                    "total_price": record.total_price,
                    "price": record.unit_price,
                }
                for record in deal_records
            ]
            log.info(
                "成交本地复核: 原始 %d 条 -> 请求面积 %.1f㎡ ±%.1f㎡ 且近%d个月命中 %d 条",
                len(all_deals),
                area,
                constants.DEAL_AREA_TOLERANCE,
                constants.DEAL_LOOKBACK_MONTHS,
                len(deal_records),
            )
            return PlatformResult(
                name="行舟深房",
                status=PlatformResultStatus.SUCCESS,
                quote_prices=quote_prices,
                deal_prices=deal_prices,
                deal_records=deal_record_dicts,
                listing_snapshots=filtered_snapshots,
                deal_source="成交记录" if deal_prices else "无",
                request_id=request_id,
                elapsed_seconds=round(time.time() - started_at, 2),
                **reference,
            )
        except Blocked as exc:
            log.warning("行舟深房命中风控: %s", exc)
            if allow_manual_retry and await self._wait_api_risk(str(exc), request_id):
                refreshed = self._token
                if refreshed:
                    return await self.collect_one(
                        client, community, area, refreshed, request_id, started_at,
                        allow_manual_retry=False,
                    )
            return short_circuit_result(
                "行舟深房", PlatformResultStatus.WAIT_MANUAL_VERIFY,
                str(exc), request_id, started_at,
            )
        except LoginExpired as exc:
            self._token = None
            log.warning("行舟深房登录已失效: %s", exc)
            await notify_manual_verify_state(
                "行舟深房(xzsfbj)/接口",
                PlatformHealthStatus.WAIT_LOGIN,
                str(exc),
            )
            return short_circuit_result(
                "行舟深房", PlatformResultStatus.LOGIN_EXPIRED,
                str(exc), request_id, started_at,
            )
        except (httpx.HTTPError, ApiResponseError, ValueError, TypeError, KeyError) as exc:
            log.exception("行舟深房采集异常")
            return short_circuit_result(
                "行舟深房", PlatformResultStatus.ERROR,
                str(exc), request_id, started_at,
            )

    @staticmethod
    def _merge_candidate_results(
        results: list[PlatformResult],
        request_id: Optional[str],
        started_at: float,
    ) -> PlatformResult:
        """Merge residential phases into one standard platform result.

        xzsfbj stores each phase under a separate regionId.  This platform
        detail stays inside the adapter: service/core.algorithm receive one
        normal PlatformResult and naturally weight every listing/deal record.
        """
        snapshots = deduplicate_same_platform(
            snapshot
            for result in results
            for snapshot in result.listing_snapshots
        )
        quote_prices = [
            float(snapshot.unit_price)
            for snapshot in snapshots
            if snapshot.unit_price is not None and snapshot.unit_price > 0
        ]
        deal_prices = [
            float(price)
            for result in results
            for price in result.deal_prices
            if price is not None and price > 0
        ]
        deal_records = [
            record
            for result in results
            for record in result.deal_records
        ]

        reference_results = [
            result
            for result in results
            if result.reference_code
            and result.reference_area_min is not None
            and result.reference_area_max is not None
        ]
        reference: dict[str, object] = {}
        if reference_results:
            reference = {
                "reference_code": reference_results[0].reference_code,
                "reference_area_tolerance": max(
                    result.reference_area_tolerance or 0.0
                    for result in reference_results
                ),
                "reference_area_min": min(
                    result.reference_area_min for result in reference_results
                    if result.reference_area_min is not None
                ),
                "reference_area_max": max(
                    result.reference_area_max for result in reference_results
                    if result.reference_area_max is not None
                ),
                "reference_listing_count": sum(
                    result.reference_listing_count or 0
                    for result in reference_results
                ),
            }

        return PlatformResult(
            name="行舟深房",
            status=PlatformResultStatus.SUCCESS,
            quote_prices=quote_prices,
            deal_prices=deal_prices,
            deal_records=deal_records,
            listing_snapshots=snapshots,
            deal_source="成交记录" if deal_prices else "无",
            reason=f"已合并 {len(results)} 个住宅期数",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
            **reference,
        )

    async def collect(self, request: InquiryRequest) -> PlatformResult:
        """执行单个请求；token 与刷新计数仅保存在当前 adapter 内存。

        同一行政区的多个住宅期数在这里合并，避免把平台索引拆分方式
        泄漏到 service 或共享算法。
        """
        started_at = time.time()
        try:
            communities = self._load_communities()
            candidates = parsers.find_community_candidates(
                communities,
                request.community_name,
                request.administrative_district,
            )
            if not candidates or (
                len(candidates) > 1 and not request.administrative_district
            ):
                district_hint = (
                    f"，行政区={request.administrative_district}"
                    if request.administrative_district
                    else ""
                )
                reason = (
                    f"小区匹配不唯一或不存在: {request.community_name}{district_hint} "
                    f"（候选 {len(candidates)} 个，请使用标准名或别名）"
                )
                log.warning("行舟深房 %s", reason)
                return short_circuit_result(
                    "行舟深房", PlatformResultStatus.NO_DATA,
                    reason, request.request_id, started_at,
                )
            successful_results: list[PlatformResult] = []
            non_data_results: list[PlatformResult] = []
            with httpx.Client(trust_env=False, timeout=15) as client:
                for index, candidate in enumerate(candidates, start=1):
                    token = await self._get_token(
                        reason=(
                            "token reached the per-batch limit; recapture it from the mini program"
                            if self._communities_since_refresh >= constants.MAX_COMMUNITIES_PER_TOKEN
                            else ""
                        )
                    )
                    if not token:
                        return short_circuit_result(
                            "行舟深房", PlatformResultStatus.WAIT_MANUAL_VERIFY,
                            "未捕获到 token，请重新打开小程序并进入成交记录",
                            request.request_id, started_at,
                        )
                    await self._wait_between_communities()
                    self._communities_since_refresh += 1
                    log.info(
                        "collect residential phase [%d/%d]: %s | regionId=%s",
                        index, len(candidates), candidate.get("name", ""),
                        candidate.get("regionId", ""),
                    )
                    result = await self.collect_one(
                        client, candidate, request.area, token,
                        request.request_id, started_at,
                    )
                    self._last_collect_finished_at = time.monotonic()
                    if result.status == PlatformResultStatus.SUCCESS:
                        successful_results.append(result)
                    elif result.status in {
                        PlatformResultStatus.WAIT_MANUAL_VERIFY,
                        PlatformResultStatus.LOGIN_EXPIRED,
                        PlatformResultStatus.ERROR,
                    }:
                        return result
                    else:
                        non_data_results.append(result)
                        log.info(
                            "phase has no usable data; continue: %s | %s",
                            candidate.get("name", ""), result.reason or result.status,
                        )
            self._last_collect_finished_at = time.monotonic()
            if successful_results:
                merged = self._merge_candidate_results(
                    successful_results, request.request_id, started_at
                )
                log.info(
                    "merged residential phases: %d/%d, listings=%d, deals=%d",
                    len(successful_results), len(candidates),
                    len(merged.quote_prices), len(merged.deal_prices),
                )
                return merged

            status = (
                PlatformResultStatus.NO_MATCHING_AREA
                if non_data_results
                and all(
                    result.status == PlatformResultStatus.NO_MATCHING_AREA
                    for result in non_data_results
                )
                else PlatformResultStatus.NO_DATA
            )
            reason = "；".join(
                result.reason or str(result.status) for result in non_data_results
            ) or "所有住宅期数均无可用数据"
            return short_circuit_result(
                "行舟深房", status, reason,
                request.request_id, started_at,
            )
        except FileNotFoundError as exc:
            return short_circuit_result(
                "行舟深房", PlatformResultStatus.ERROR,
                str(exc), request.request_id, started_at,
            )
        except (OSError, ApiResponseError, ValueError, TypeError, KeyError) as exc:
            log.exception("行舟深房准备采集失败")
            return short_circuit_result(
                "行舟深房", PlatformResultStatus.ERROR,
                str(exc), request.request_id, started_at,
            )
