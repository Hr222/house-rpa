# -*- coding: utf-8 -*-
"""安居客平台采集适配逻辑。

业务流程与贝壳一致（搜索→筛选→抓在售→算最终价），但因平台特性有以下差异：
- 面积筛选：自定义输入框填值（贝壳是点预设档位 a1-a7）
- 无分页：单页全展示，滚动到底即可（贝壳要翻页）
- 无成交记录：安居客不对外展示成交，业务上把挂牌均价（社区卡片）
  顶替 deal_prices，保留平台无成交记录时的业务数据兼容字段。
  代码注释里已标明这一特殊处理。
- 不点详情：挂牌均价在结果页就有，无需进小区详情页（平台差异，非删流程）。

采集逻辑移植自 ajk_mvp_test.py 全链路验证通过的实现。
"""

from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Optional

from app.rpa.core.status import PlatformResultStatus
from app.rpa.core.models import PlatformResult
from app.rpa.platforms.ajk import parser as parsers
from app.rpa.utils.debug_utils import dump_html
from app.rpa.platforms.base import has_matching_community_snapshots, wait_and_reload_after_block
from app.rpa.platforms.city_map import get_start_url
from urllib.parse import urlparse


log = logging.getLogger(__name__)
def _is_captcha_url(url: str) -> bool:
    """安居客 58 系验证码拦截页 URL 特征。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def _is_captcha_html(html: str) -> bool:
    """安居客(58系)验证码拦截页 HTML 特征。

    真实样本：callback.58.com/antibot/verifycode，58 自家 ISDCaptcha SDK。
    """
    markers = (
        "请输入验证码",          # <title> 文案（ws:IP 动态后缀，单靠不稳）
        'id="ISDCaptcha"',      # 58 验证码 SDK 容器（最稳的结构标识）
        'class="code_img"',     # 验证码图片容器
    )
    return any(marker in (html or "") for marker in markers)


def _is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url or "signin" in url


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """安居客风控/登录检测。"""
    if _is_captcha_url(url) or _is_captcha_html(html or ""):
        return True, "命中验证码拦截"
    if _is_login_url(url):
        return True, "命中登录页"
    return False, ""


def is_no_result(html: str) -> bool:
    """空态判定：安居客"没有找到相关房源"空态 section。

    真实 dump（2026-09-03 洪湖14号大院直达页 20260903_142248）：
    <section class="empty"><span class="empty-text">没有找到相关房源，可
    …</span></section>，其后为 60 张别的小区推荐卡。空态页解析前必须
    短路；已核对 8 份正常结果页 dump 均不含该文案，无误伤。
    """
    return "没有找到相关房源" in (html or "")


# ============================================================
# 页面交互辅助
# ============================================================

async def _dump(page, name: str):
    """调试模式下导出页面 HTML。"""
    await dump_html(page, name, logger=log)


async def reset_to_start_page(page, city: str = "深圳"):
    """回到安居客二手房首页，并获取新的页面上下文。"""
    url = get_start_url("ajk", city)
    refreshed_page = await page.get(url)
    await refreshed_page
    await asyncio.sleep(2)
    return refreshed_page


# ============================================================
# 就绪检测 / 保活
# ============================================================

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
    """由第 1 页推导后续页 URL（安居客 /sale/p{N}/?comm_id=，无点击）。

    页码取自 HTML 中指向同一小区（comm_id 一致）的分页链接；
    只保留第 2 页起并套 max_pages 兜底。与 MVP build_page_urls 同源。
    """
    parsed = urlparse(listing_url)
    origin = f"{parsed.scheme}://{parsed.hostname}"
    comm_id_m = re.search(r"comm_id=(\d+)", listing_url)
    if comm_id_m is None:
        return []
    comm_id = comm_id_m.group(1)
    page_nums = sorted(
        {
            int(number)
            for number in re.findall(
                rf"/sale/p(\d+)/\?comm_id={comm_id}\b", first_page_html or ""
            )
        }
    )
    return [
        f"{origin}/sale/p{page_no}/?comm_id={comm_id}"
        for page_no in page_nums
        if 2 <= page_no <= max_pages
    ]


async def collect_listing_by_url(
    page,
    *,
    community_name: str,
    listing_page_url: str,
    request_id: Optional[str] = None,
    max_pages: int = 50,
) -> PlatformResult:
    """URL 直达采集小区在售（非交互 HTML 提取方式，与 MVP 模板等价）。

    流程：直达挂牌页 → 风控协议 → is_no_result 空态短路 → 解析在售与
    挂牌均价 → 归属校验 → 无点击翻页 → parser 提取。安居客无成交，
    挂牌均价按业务顶替 deal_prices（deal_source="挂牌均价顶替"）。
    """
    start = time.time()
    try:
        await page.get(listing_page_url)
        await page
        await asyncio.sleep(3)

        html = await wait_and_reload_after_block(
            page, detect_block, f"小区挂牌页[{community_name}]"
        )
        await _dump(page, "ajk_listing_by_url_p1")

        if is_no_result(html):
            log.info("[空态] %s 在安居客在售 0 条（跳过推荐位）", community_name)
            return PlatformResult(
                name="安居客",
                status=PlatformResultStatus.NO_DATA,
                community_avg_price=parsers.parse_community_avg_price(html),
                reason="在售 0 条",
                listing_page_url=listing_page_url,
                request_id=request_id,
                elapsed_seconds=round(time.time() - start, 2),
            )

        snapshots = parsers.parse_listing_snapshots(html)
        community_avg_price = parsers.parse_community_avg_price(html)
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
            await _dump(page, f"ajk_listing_by_url_p{page_no}")
            page_snapshots = parsers.parse_listing_snapshots(page_html)
            snapshots.extend(page_snapshots)
            if not page_snapshots:
                log.info("[翻页] 第 %d 页无在售，停止翻页", page_no)
                break
            log.info("[翻页] 第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_snapshots), len(snapshots))

        status = PlatformResultStatus.SUCCESS if snapshots else PlatformResultStatus.NO_DATA
        # RPA 语义收窄：挂牌均价为页面抓取原值（仅溯源），不再顶替 deal_prices
        log.info(
            "[采集汇总] %s：在售 %d 条，挂牌均价 %s 元/㎡",
            community_name, len(snapshots),
            f"{community_avg_price:.0f}" if community_avg_price else "未识别",
        )
        return PlatformResult(
            name="安居客",
            status=status,
            community_avg_price=community_avg_price,  # 页面卡片原值，仅溯源
            quote_prices=[],
            deal_prices=[],
            deal_source="挂牌均价顶替",
            request_id=request_id,
            listing_page_url=listing_page_url,
            elapsed_seconds=round(time.time() - start, 2),
            listing_snapshots=snapshots,
        )
    except Exception as exc:
        log.exception("安居客 URL 直达采集异常：%s", exc)
        return PlatformResult(
            name="安居客",
            status=PlatformResultStatus.ERROR,
            reason=str(exc),
            listing_page_url=listing_page_url,
            request_id=request_id,
            elapsed_seconds=round(time.time() - start, 2),
        )
