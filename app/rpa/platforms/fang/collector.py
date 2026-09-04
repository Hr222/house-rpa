# -*- coding: utf-8 -*-
"""房天下平台采集适配逻辑。

业务流程与贝壳一致（搜索→筛选→抓在售→点详情→抓成交→算最终价），
但因平台特性有以下差异：
- 面积筛选：自定义输入框填值（贝壳是点预设档位 a1-a7）
- 有分页：需翻页采集（贝壳也要翻页；安居客无分页）
- 详情入口只在第一页有：分页前先 Ctrl+点击打开详情（后台新标签），
  采完在售再切到详情标签抓成交（方案B）。
- 成交记录在详情页的"小区成交"tab，同页跳转到 /loupan/{id}/chengjiao/。
- 成交筛选规则：请求面积 ±5㎡ + 近半年；不使用在售页面的宽面积档位。

采集逻辑移植自 fang_mvp_test.py 全链路验证通过的实现。
"""

from __future__ import annotations
import asyncio
import logging
import re
import time
from html import unescape
from typing import Optional
from urllib.parse import urljoin, urlparse

from app.rpa.core.status import PlatformResultStatus
from app.rpa.core.models import PlatformResult
from app.rpa.platforms.fang import parser as parsers
from app.rpa.utils.debug_utils import dump_html
from app.rpa.platforms.base import (
    _human_click,
    has_matching_community_snapshots,
    short_circuit_result,
    wait_and_reload_after_block,
)
from app.rpa.platforms.city_map import get_start_url
from app.rpa.platforms.fang.constants import FANG_SOFT_BLOCK_SECONDS


log = logging.getLogger(__name__)
def _is_captcha_url(url: str) -> bool:
    """房天下验证码拦截页 URL 特征（具体待 dump 核对后收敛）。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def _is_captcha_html(html: str) -> bool:
    """只识别真实人机验证提示，排除详情页正常的短信验证码表单。"""
    markers = (
        "验证后继续访问",
        "请完成验证",
        "滑动验证",
        "访问过于频繁",
    )
    return any(marker in html for marker in markers)


def _is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url or "signin" in url


# 登录墙 HTML 标记（2026-09-04 自 fang_community_page_mvp 实测标记提升）：
# 房天下会话失效时正文出现登录引导，而 URL 不含 login/passport。
_LOGIN_HTML_MARKERS = ("请输入手机号", "请输入密码", "手机快捷登录", "扫码登录")


def _is_login_html(html: str) -> bool:
    return any(marker in (html or "") for marker in _LOGIN_HTML_MARKERS)


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """房天下风控/登录检测。"""
    if _is_captcha_url(url) or _is_captcha_html(html or ""):
        return True, "命中验证码拦截"
    if _is_login_url(url) or _is_login_html(html or ""):
        return True, "命中登录页"
    return False, ""


class FangSoftBlockError(RuntimeError):
    """房天下软风控：页面可访问、无验证码，但内容超过阈值仍未渲染。"""


def check_soft_block_elapsed(started_at: float, label: str) -> None:
    """软风控判定：单次"打开页面→可解析"耗时超过 FANG_SOFT_BLOCK_SECONDS 即抛出。"""
    elapsed = time.monotonic() - started_at
    if elapsed >= FANG_SOFT_BLOCK_SECONDS:
        raise FangSoftBlockError(
            f"{label}耗时 {elapsed:.0f}s ≥ {FANG_SOFT_BLOCK_SECONDS:.0f}s，判定触发软风控"
        )


def is_no_result(html: str) -> bool:
    """空态判定：房天下挂牌聚合页"本小区无在售房源"。

    marker 基于真实空态 dump 校准（莲通公司综合楼 house-xm2810134960，
    2026-09-03 存档 debug/20260903_1805_fang_listing_空态_*.html）：
    空态页主体有 <div class="shop_no"> 容器，内含文案
    "很抱歉，没有找到<span>…</span>相符的房源！"；同页仍渲染其他小区的
    推荐房源 dl 卡（鸣乐大厦等），因此不能按"无在售卡"判断。

    三个特征组合判定（shop_no 容器 + 两句文案）才能与单条数据页区分：
    单条页（莲塘派出所宿舍 house-xm2811125778）也有 shop_no，但那是
    隐形弹窗（pop_up/close_pop），不含"很抱歉，没有找到"，故判非空态。
    """
    text = html or ""
    return (
        "shop_no" in text
        and "很抱歉，没有找到" in text
        and "相符的房源" in text
    )


# ============================================================
# 页面交互辅助
# ============================================================

async def _dump(page, name: str):
    """调试模式下导出页面 HTML。"""
    await dump_html(page, name, logger=log)


async def reset_to_start_page(page, city: str = "深圳"):
    """回到房天下二手房首页，并获取新的页面上下文。"""
    url = get_start_url("fang", city)
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


def _id_token_from_url(page_url: str) -> Optional[str]:
    """从房天下挂牌/成交入口 URL 提取小区数字 ID（house-xm{ID} / loupan/{ID}）。"""
    m = re.search(r"/(?:house-xm)(\d+)", page_url or "") or re.search(
        r"/loupan/(\d+)", page_url or ""
    )
    return m.group(1) if m else None


def _build_pagination_urls(page_url: str, first_page_html: str, max_pages: int = 50) -> list[str]:
    """由第 1 页原生分页链接收集后续页 URL（挂牌/成交通用，无点击）。

    只收录包含同一小区数字 ID 的链接（页号取链接文本），urljoin 还原。
    与 MVP build_page_urls 同源。
    """
    token = _id_token_from_url(page_url)
    if token is None:
        return []
    page_urls: list[tuple[int, str]] = []
    seen_pages: set[int] = set()
    for match in re.finditer(
        r'<a[^>]*href="([^"]+)"[^>]*>\s*(\d+)\s*</a>',
        first_page_html or "",
    ):
        href = urljoin(page_url, unescape(match.group(1)))
        path = urlparse(href).path
        page_no = int(match.group(2))
        if token not in path:
            continue
        if page_no in seen_pages or page_no < 2 or page_no > max_pages:
            continue
        seen_pages.add(page_no)
        page_urls.append((page_no, href))
    return [href for _, href in sorted(page_urls)]


async def _click_page_by_text(page, page_no: int) -> Optional[str]:
    """回退点击：按页码文本点击，返回加载完成后的 HTML；无法翻页返回 None。"""
    try:
        elements = await page.select_all("div.page_box a", timeout=3)
    except Exception:
        elements = []
    target = None
    for el in elements:
        try:
            text = await el.apply("(el) => el.textContent.trim()")
        except Exception:
            text = ""
        if text == str(page_no):
            target = el
            break
    if target is None:
        return None
    if not await _human_click(page, target, f"deal page {page_no}"):
        return None
    await page
    await asyncio.sleep(3)
    return await page.get_content()


async def _collect_deals_by_url(
    page,
    *,
    community_name: str,
    deal_page_url: str,
    max_pages: int = 50,
) -> list[dict]:
    """直达成交页采集真实成交（全量，键名对齐入库层 {area,date,total_price,price}）。

    成交分页优先 URL 直达；无原生链接但声明多页时回退点击。与 MVP 同源。
    """
    deals: list[dict] = []
    await page.get(deal_page_url)
    await page
    await asyncio.sleep(3)
    first_html = await wait_and_reload_after_block(
        page, detect_block, f"小区成交页[{community_name}]"
    )
    await _dump(page, "fang_deal_by_url_p1")

    raw = parsers.parse_deal_records(first_html)
    deals = [
        {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
        for r in raw if r[0] is not None and r[3] is not None
    ]
    log.info("[成交] %s 第 1 页真实成交 %d 条", community_name, len(deals))

    page_urls = _build_pagination_urls(deal_page_url, first_html, max_pages)
    total_pages = parsers.parse_total_pages(first_html)
    if page_urls:
        log.info("[成交] 另有 %d 页待采（URL 直达）", len(page_urls))
        for page_no, page_url in enumerate(page_urls, start=2):
            await page.get(page_url)
            await page
            await asyncio.sleep(2)
            page_html = await wait_and_reload_after_block(
                page, detect_block, f"成交翻页第 {page_no} 页"
            )
            await _dump(page, f"fang_deal_by_url_p{page_no}")
            page_raw = parsers.parse_deal_records(page_html)
            page_deals = [
                {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
                for r in page_raw if r[0] is not None and r[3] is not None
            ]
            deals.extend(page_deals)
            if not page_deals:
                log.info("[成交] 第 %d 页无成交记录，停止翻页", page_no)
                break
            log.info("[成交] 第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_deals), len(deals))
    elif total_pages > 1:
        log.info("[成交] 页面无 URL 分页链接（共 %d 页），回退点击翻页", total_pages)
        for page_no in range(2, total_pages + 1):
            page_html = await _click_page_by_text(page, page_no)
            if page_html is None:
                log.warning("[成交] 第 %d 页无法翻页，停止", page_no)
                break
            page_html = await wait_and_reload_after_block(
                page, detect_block, f"成交点击第 {page_no} 页"
            )
            await _dump(page, f"fang_deal_by_url_p{page_no}")
            page_raw = parsers.parse_deal_records(page_html)
            page_deals = [
                {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
                for r in page_raw if r[0] is not None and r[3] is not None
            ]
            deals.extend(page_deals)
            if not page_deals:
                break
    log.info("[成交汇总] %s：真实成交 %d 条", community_name, len(deals))
    return deals


async def collect_listing_by_url(
    page,
    *,
    community_name: str,
    listing_page_url: str,
    deal_page_url: Optional[str] = None,
    request_id: Optional[str] = None,
    max_pages: int = 50,
    city: str = "深圳",
) -> PlatformResult:
    """URL 直达采集小区在售与真实成交（非交互，与 MVP 模板等价）。

    在售：直达挂牌页 → 风控 → 软风控判定 → is_no_result 空态 → 归属 →
    无点击翻页。成交：deal_page_url 提供时独立直达采集全量真实成交
    （挂牌空态也采）。只回传原始明细（listing_snapshots/deal_records）；
    成交面积收敛与估价由算法层（aggregation → deal_screening）接管。

    软风控规则（FANG_SOFT_BLOCK_SECONDS）：打开挂牌页后内容超时未渲染，
    先回首页刷新会话再直达一次；仍超时按 ERROR 返回，交人工处理。
    """
    start = time.time()
    soft_block_refreshed = False
    try:
        while True:
            opened_at = time.monotonic()
            await page.get(listing_page_url)
            await page
            await asyncio.sleep(3)
            html = await wait_and_reload_after_block(
                page, detect_block, f"小区挂牌页[{community_name}]"
            )
            try:
                check_soft_block_elapsed(opened_at, f"挂牌页[{community_name}]")
            except FangSoftBlockError:
                if soft_block_refreshed:
                    log.error(
                        "[软风控] %s：会话刷新后再次超时（≥%.0fs），按失败返回",
                        community_name,
                        FANG_SOFT_BLOCK_SECONDS,
                    )
                    return short_circuit_result(
                        "房天下",
                        PlatformResultStatus.ERROR,
                        f"软风控：挂牌页连续两次 {FANG_SOFT_BLOCK_SECONDS:.0f}s 未渲染，"
                        "建议人工重置浏览器会话后重试",
                        request_id,
                        start,
                        detail_url=listing_page_url,
                    )
                soft_block_refreshed = True
                log.warning(
                    "[软风控] %s：打开挂牌页超过 %.0fs 内容未就绪，回首页刷新会话后重试一次",
                    community_name,
                    FANG_SOFT_BLOCK_SECONDS,
                )
                page = await reset_to_start_page(page, city)
                continue
            break
        await _dump(page, "fang_listing_by_url_p1")

        if is_no_result(html):
            log.info("[空态] %s 在房天下在售 0 条（跳过推荐位）", community_name)
            listing_snapshots = []
        else:
            listing_snapshots = parsers.parse_listing_snapshots(html, base_url=page.target.url)
            if listing_snapshots and not has_matching_community_snapshots(listing_snapshots, community_name):
                log.warning(
                    "[归属不符] %s：%s 页面快照与目标小区不匹配（%d 条全部弃用）",
                    community_name, listing_page_url, len(listing_snapshots),
                )
                listing_snapshots = []

        if listing_snapshots:
            page_urls = _build_pagination_urls(listing_page_url, html, max_pages)
            if page_urls:
                log.info("[翻页] 在售第 1 页 %d 条；另有 %d 页待采", len(listing_snapshots), len(page_urls))
            for page_no, page_url in enumerate(page_urls, start=2):
                await page.get(page_url)
                await page
                await asyncio.sleep(2)
                page_html = await wait_and_reload_after_block(
                    page, detect_block, f"在售翻页第 {page_no} 页"
                )
                await _dump(page, f"fang_listing_by_url_p{page_no}")
                page_snapshots = parsers.parse_listing_snapshots(page_html, base_url=page.target.url)
                listing_snapshots.extend(page_snapshots)
                if not page_snapshots:
                    log.info("[翻页] 在售第 %d 页无房源，停止翻页", page_no)
                    break

        deals: list[dict] = []
        if deal_page_url:
            deals = await _collect_deals_by_url(
                page, community_name=community_name,
                deal_page_url=deal_page_url, max_pages=max_pages,
            )

        # RPA 语义收窄：成交只回传原始明细，面积收敛由算法层接管
        status = PlatformResultStatus.SUCCESS if (listing_snapshots or deals) else PlatformResultStatus.NO_DATA
        log.info("[采集汇总] %s：在售 %d 条，成交 %d 条", community_name, len(listing_snapshots), len(deals))
        return PlatformResult(
            name="房天下",
            status=status,
            quote_prices=[],
            deal_prices=[],
            deal_records=deals,
            deal_source="成交记录" if deals else "无",
            request_id=request_id,
            listing_page_url=listing_page_url,
            deal_page_url=deal_page_url,
            elapsed_seconds=round(time.time() - start, 2),
            listing_snapshots=listing_snapshots,
        )
    except Exception as exc:
        log.exception("房天下 URL 直达采集异常：%s", exc)
        return PlatformResult(
            name="房天下",
            status=PlatformResultStatus.ERROR,
            reason=str(exc),
            listing_page_url=listing_page_url,
            deal_page_url=deal_page_url,
            request_id=request_id,
            elapsed_seconds=round(time.time() - start, 2),
        )
