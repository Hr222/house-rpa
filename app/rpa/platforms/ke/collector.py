# -*- coding: utf-8 -*-
"""贝壳平台采集适配逻辑。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from app.rpa.core.status import PlatformResultStatus
from app.rpa.core.models import PlatformResult
from app.rpa.platforms.ke import parser as parsers
from app.rpa.utils.debug_utils import dump_html
from app.rpa.platforms.base import has_matching_community_snapshots, wait_and_reload_after_block
from app.rpa.platforms.city_map import get_start_url
from html import unescape
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)


async def _dump(page, name: str):
    """调试模式下导出页面 HTML。"""
    await dump_html(page, name, logger=log)


async def _reset_to_start_page(page, city: str = "深圳"):
    """回到贝壳二手房首页，并获取新的页面上下文。"""
    url = get_start_url("ke", city)
    refreshed_page = await page.get(url)
    await refreshed_page
    await asyncio.sleep(2)
    return refreshed_page


async def reset_to_start_page(page, city: str = "深圳"):
    return await _reset_to_start_page(page, city)


def _is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url or "clogin.ke.com" in url


def _is_login_html(html: str) -> bool:
    markers = (
        'meta name="ke-passport" content="LOGIN"',
        'id="login"',
    )
    return any(marker in html for marker in markers)


# 登录失效检测：贝壳/链家共用 ljConf，未登录时后端注入的 ucid 为空字符串
# 真实样本：未登录 ucid:'' / 已登录 ucid:'2000000547667569'
# 用"紧跟 cdn"锚定 ljConf 上下文，排除页面里其它无关的 ucid:''（SDK 配置项）
_KE_NOT_LOGIN_PATTERN = re.compile(r"ucid\s*:\s*''\s*,?\s*cdn", re.S)


def _is_login_expired_html(html: str) -> bool:
    """贝壳登录失效（软失效）：ljConf 内 ucid 为空。

    贝壳与链家共用 ljConf 前端框架，登录态判据一致。
    软失效指登录掉了但 URL 没变（弹窗形式），URL/HTML 硬检测抓不到。
    "紧跟 cdn" 锚定 ljConf，避免误匹配页面里其它无关的 ucid:''。
    """
    return bool(_KE_NOT_LOGIN_PATTERN.search(html or ""))


def _is_captcha_url(url: str) -> bool:
    """贝壳验证码拦截页 URL 特征（贝壳/链家共用 hip 安全系统）。

    真实样本：https://hip.lianjia.com/captcha?location=...
    贝壳对应 hip.ke.com（同一套安全系统），/captcha 作兜底。
    """
    url = (url or "").lower()
    return "hip.ke.com/captcha" in url or "/captcha" in url


def _is_manual_verify_html(html: str) -> bool:
    """贝壳验证码拦截页 HTML 特征（基于真实 dump 样本）。

    hip 安全中心 + 极验 geetest SDK，贝壳/链家共用同一套页面。
    注意：只留验证码页专属标识。"贝壳信息安全中心"(页脚版权)和
    "hip-static"(静态资源路径)在正常结果页也存在，曾导致正常页误判成
    验证码（见 2026-07-17 中海怡翠山庄 case），已移除。
    """
    markers = (
        "<title>CAPTCHA</title>",       # 验证码页 title（铁证，正常页不会有）
        "captcha.lianjia.com",          # window.captchaEndpoint JS 变量
        'alt="CAPTCHA"',                 # <img class="bg" alt="CAPTCHA">
    )
    return any(marker in (html or "") for marker in markers)


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """贝壳风控/登录检测。

    贝壳区分人机验证和登录失效：
    - 人机验证 → (True, "命中验证码拦截")
    - 登录失效 → (True, "命中登录页")
    - 正常     → (False, "")
    登录检测覆盖三种形态：硬失效(URL) + 登录页HTML(ke-passport/id=login) + 软失效(ucid为空)。
    """
    if _is_captcha_url(url or "") or _is_manual_verify_html(html or ""):
        return True, "命中验证码拦截"
    if (_is_login_url(url or "") or _is_login_html(html or "")
            or _is_login_expired_html(html or "")):
        return True, "命中登录页"
    return False, ""


def is_no_result(html: str) -> bool:
    """空态判定：贝壳"暂无在售房源"页（与链家共用同一套列表组件）。

    真实 dump（泰福名苑 c24000000024228）：<div class="m-noresult">当前
    小区暂无在售房源，为您推荐附近小区房源</div>，其后 sellListContent
    是别的小区推荐位。空态页仍有小区详情/列表结构，必须靠本 marker
    在解析前短路，绝不能把推荐位当在售解析。
    """
    return "m-noresult" in (html or "")


async def probe_ready(main_page) -> tuple[bool, str]:
    """轻量就绪探测（URL 直达时代）：页面可用且未风控即 READY。"""
    try:
        await main_page.select("body", timeout=8)
        await main_page
        html = await main_page.get_content()
    except Exception as exc:
        return False, f"页面不可用: {exc}"
    blocked, reason = detect_block(main_page.target.url or "", html)
    if blocked:
        return False, reason
    return True, "READY"


def _build_listing_pagination_urls(listing_url: str, first_page_html: str, max_pages: int = 50) -> list[str]:
    """由第 1 页分页链接收集后续页 URL（贝壳 /ershoufang/pg{N}c{ID}/，无点击）。

    只收录携带同一小区 token（c{ID}）的链接，避免误采全市/商圈翻页；
    无分页链接返回空列表（单页小区）。与 MVP build_page_urls 同源。
    """
    parsed = urlparse(listing_url)
    token_m = re.search(r"/ershoufang/(c\d+)", parsed.path)
    if token_m is None:
        return []
    token = token_m.group(1)
    page_urls: list[str] = []
    seen_pages: set[int] = set()
    for match in re.finditer(r'href="([^"]*pg(\d+)[^"]*)"', first_page_html or ""):
        href = urljoin(listing_url, unescape(match.group(1)))
        path = urlparse(href).path
        num_m = re.search(r"pg(\d+)", path)
        if num_m is None or token not in path:
            continue
        page_no = int(num_m.group(1))
        if page_no in seen_pages or page_no < 2 or page_no > max_pages:
            continue
        seen_pages.add(page_no)
        page_urls.append(href)
    return page_urls


async def collect_listing_by_url(
    page,
    *,
    community_name: str,
    listing_page_url: str,
    request_id: Optional[str] = None,
    max_pages: int = 50,
) -> PlatformResult:
    """URL 直达采集小区在售（非交互 HTML 提取方式，与 MVP 模板等价）。

    流程：直达挂牌页 → 风控协议 → is_no_result 空态短路 → 归属校验 →
    无点击翻页（_build_listing_pagination_urls）→ parser 提取。
    贝壳不采成交与均价；返回全量目标小区快照与单价镜像
    （面积口径留给消费方，与批量编排 evaluate_stage 口径一致）。
    风控命中在协议内暂停等人工，返回即页面已干净。
    """
    start = time.time()
    try:
        await page.get(listing_page_url)
        await page
        await asyncio.sleep(3)

        html = await wait_and_reload_after_block(
            page, detect_block, f"小区挂牌页[{community_name}]"
        )
        await _dump(page, "ke_listing_by_url_p1")

        if is_no_result(html):
            log.info("[空态] %s 在贝壳在售 0 条（跳过推荐位）", community_name)
            return PlatformResult(
                name="贝壳",
                status=PlatformResultStatus.NO_DATA,
                reason="在售 0 条",
                listing_page_url=listing_page_url,
                request_id=request_id,
                elapsed_seconds=round(time.time() - start, 2),
            )

        snapshots = parsers.parse_listing_snapshots(html, base_url=page.target.url)
        if snapshots and not has_matching_community_snapshots(snapshots, community_name):
            log.warning(
                "[归属不符] %s：%s 页面快照与目标小区不匹配（%d 条全部弃用）",
                community_name, listing_page_url, len(snapshots),
            )
            snapshots = []

        page_urls = (
            _build_listing_pagination_urls(listing_page_url, html, max_pages)
            if snapshots else []
        )
        if page_urls:
            log.info("[翻页] 第 1 页 %d 条；另有 %d 页待采", len(snapshots), len(page_urls))
        for page_no, page_url in enumerate(page_urls, start=2):
            await page.get(page_url)
            await page
            await asyncio.sleep(2)
            page_html = await wait_and_reload_after_block(
                page, detect_block, f"翻页第 {page_no} 页"
            )
            await _dump(page, f"ke_listing_by_url_p{page_no}")
            page_snapshots = parsers.parse_listing_snapshots(page_html, base_url=page.target.url)
            snapshots.extend(page_snapshots)
            if not page_snapshots:
                log.info("[翻页] 第 %d 页无在售，停止翻页", page_no)
                break

        status = PlatformResultStatus.SUCCESS if snapshots else PlatformResultStatus.NO_DATA
        log.info(
            "[采集汇总] %s：在售 %d 条（贝壳 URL 直达，不采成交与均价）",
            community_name, len(snapshots),
        )
        return PlatformResult(
            name="贝壳",
            status=status,
            # RPA 语义收窄：不产出价格加工聚合，原始单价在 listing_snapshots
            quote_prices=[],
            listing_snapshots=snapshots,
            listing_page_url=listing_page_url,
            request_id=request_id,
            elapsed_seconds=round(time.time() - start, 2),
        )
    except Exception as exc:
        log.exception("贝壳 URL 直达采集异常：%s", exc)
        return PlatformResult(
            name="贝壳",
            status=PlatformResultStatus.ERROR,
            reason=str(exc),
            listing_page_url=listing_page_url,
            request_id=request_id,
            elapsed_seconds=round(time.time() - start, 2),
        )
