# -*- coding: utf-8 -*-
"""贝壳平台采集适配逻辑。"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Optional

import config
from app import parsers
from app.debug_utils import dump_html
from app.models import ListingSnapshot, PlatformResult
from app.platforms.ke_constants import AREA_SEGMENTS, START_URL

log = logging.getLogger(__name__)

PAGE_LINGER_SECONDS = config.PAGE_LINGER_SECONDS


async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    """真人操作间隔。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _dump(page, name: str):
    """调试模式下导出页面 HTML。"""
    await dump_html(page, name, logger=log)


async def _reset_to_start_page(page):
    """回到贝壳二手房首页，并获取新的页面上下文。"""
    refreshed_page = await page.get(START_URL)
    await refreshed_page
    await asyncio.sleep(2)
    return refreshed_page


async def reset_to_start_page(page):
    return await _reset_to_start_page(page)


def _pick_segments(area_min: float, area_max: float) -> list[str]:
    """请求区间 [min, max] 对应的面积档位。"""
    segments: list[str] = []
    for low, high, code in AREA_SEGMENTS:
        if area_min < high and area_max > low:
            segments.append(code)
    return segments


def _build_segment_candidates(area_min: float, area_max: float) -> list[str]:
    segments = _pick_segments(area_min, area_max)
    if not segments:
        raise RuntimeError(f"面积区间无可用档位: {area_min}-{area_max}")
    return segments


def _is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url or "clogin.ke.com" in url


def _is_login_html(html: str) -> bool:
    markers = (
        'meta name="ke-passport" content="LOGIN"',
        'id="login"',
        "请输入手机号",
        "请输入密码",
        "手机快捷登录",
        "请完成人机验证",
    )
    return any(marker in html for marker in markers)


def _is_manual_verify_html(html: str) -> bool:
    markers = (
        "请完成人机验证",
        "验证后继续访问",
        "安全验证",
        "点击按钮开始验证",
    )
    return any(marker in html for marker in markers)


def _extract_xiaoqu_id(detail_url: Optional[str]) -> Optional[str]:
    if not detail_url:
        return None
    match = re.search(r"/xiaoqu/(\d+)/", detail_url)
    return match.group(1) if match else None


def _parse_total_pages(html: str) -> int:
    match = re.search(
        r'page-data="\{&quot;totalPage&quot;:(\d+),&quot;curPage&quot;:(\d+)\}"',
        html or "",
    )
    return int(match.group(1)) if match else 1


def _parse_current_page(html: str) -> int:
    match = re.search(
        r'page-data="\{&quot;totalPage&quot;:(\d+),&quot;curPage&quot;:(\d+)\}"',
        html or "",
    )
    return int(match.group(2)) if match else 1


async def _is_interactable(el) -> bool:
    try:
        pos = await el.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def _pick_first(page, selectors: list[str], timeout: float = 1.5):
    for selector in selectors:
        try:
            elements = await page.select_all(selector, timeout=timeout)
        except Exception:
            continue
        if not elements:
            continue
        for el in elements:
            if await _is_interactable(el):
                return el
        return elements[0]
    return None


async def _human_click(page, element, label: str) -> bool:
    if not element:
        return False

    try:
        await element.scroll_into_view()
    except Exception:
        pass

    await _delay(0.2, 0.5)
    last_error = None
    for clicker in ("mouse", "js"):
        try:
            if clicker == "mouse":
                try:
                    await element.mouse_move()
                    await _delay(0.1, 0.3)
                except Exception:
                    pass
                await element.mouse_click()
            else:
                await element.click()
            await page
            await _delay(0.5, 1.0)
            return True
        except Exception as exc:
            last_error = exc

    log.warning("%s click failed: %s", label, last_error)
    return False


async def _click_by_candidates(
    page,
    label: str,
    selectors: Optional[list[str]] = None,
    texts: Optional[list[str]] = None,
) -> bool:
    selectors = selectors or []
    texts = texts or []

    element = await _pick_first(page, selectors)
    if element and await _human_click(page, element, label):
        return True

    for text in texts:
        try:
            element = await page.find(text, timeout=1.5)
        except Exception:
            element = None
        if element and await _human_click(page, element, label):
            return True

    return False


async def _get_search_input(page):
    selectors = [
        "#searchInput",
        "input#searchInput",
        "input[type='search']",
        "input[placeholder*='小区']",
        "input[placeholder*='搜索']",
        ".searchInput input",
    ]
    element = await _pick_first(page, selectors, timeout=2)
    if not element:
        raise RuntimeError("未找到搜索框")
    return element


async def _clear_and_type(page, inp, community_name: str):
    await _human_click(page, inp, "search input")
    try:
        await inp.clear_input()
    except Exception:
        await inp.send_keys("\uE009a")
        await inp.send_keys("\uE017")
        await page
    await _delay(0.2, 0.5)
    await inp.send_keys(community_name)
    await page
    await _delay(0.5, 1.0)


async def _submit_search(page, inp=None):
    if inp is not None:
        await inp.send_keys("\r")
    else:
        submit_btn = await _pick_first(
            page,
            ["button[type='submit']", ".searchButton", ".search-btn", ".btn-search"],
            timeout=1.5,
        )
        if not submit_btn:
            raise RuntimeError("未找到可用于提交搜索的元素")
        await _human_click(page, submit_btn, "search submit")
    await page
    await _delay(2, 4)


async def _wait_for_results_loaded(page, expected_page: Optional[int] = None) -> str:
    await page.select("ul.sellListContent", timeout=15)
    await page

    last_html = ""
    for _ in range(20):
        last_html = await page.get_content()
        if expected_page is None or _parse_current_page(last_html) == expected_page:
            await asyncio.sleep(1.2)
            return last_html
        await asyncio.sleep(0.5)

    await asyncio.sleep(1.2)
    return last_html or await page.get_content()


async def probe_ready(main_page) -> tuple[bool, str]:
    """检查当前页是否已登录且仍可执行搜索。"""
    try:
        await main_page.select("body", timeout=10)
        await main_page
        html = await main_page.get_content()
        current_url = main_page.target.url or ""
    except Exception as exc:
        return False, f"页面不可用: {exc}"

    if _is_manual_verify_html(html):
        return False, "命中人机验证，等待人工处理"
    if _is_login_url(current_url) or _is_login_html(html):
        return False, "当前会话未登录或已失效"

    try:
        await _get_search_input(main_page)
    except Exception:
        return False, "未找到搜索框，页面未就绪"
    return True, "READY"


async def keepalive(main_page) -> tuple[bool, str]:
    """轻量保活：优先探测，必要时刷新。"""
    ready, message = await probe_ready(main_page)
    if ready:
        try:
            await main_page.evaluate("window.scrollTo(0, 0);")
            await main_page
        except Exception:
            pass
        return True, "READY"

    try:
        main_page = await _reset_to_start_page(main_page)
    except Exception as exc:
        return False, f"刷新保活失败: {exc}"

    return await probe_ready(main_page)


async def _human_linger_on_result_page(page, page_no: int, linger_seconds: float = PAGE_LINGER_SECONDS):
    log.info("lingering on result page %s for %.1fs", page_no, linger_seconds)
    start = time.monotonic()
    scroll_steps = [
        ("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.25));", 1.6),
        ("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.55));", 1.8),
        ("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.82));", 1.8),
        ("window.scrollTo(0, document.body.scrollHeight);", 1.2),
    ]
    for expression, pause in scroll_steps:
        try:
            await page.evaluate(expression)
            await page
        except Exception:
            pass
        await asyncio.sleep(pause)

    remain = linger_seconds - (time.monotonic() - start)
    if remain > 0:
        await asyncio.sleep(remain)


async def _click_page_number(page, page_no: int) -> str:
    selector = f".house-lst-page-box a[data-page='{page_no}']"
    try:
        element = await page.select(selector, timeout=3)
    except Exception:
        element = None

    if not element:
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")

    if not await _human_click(page, element, f"page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")

    return await _wait_for_results_loaded(page, expected_page=page_no)


async def _collect_listing_pages(page, first_page_html: str, total_pages: int):
    all_records: dict[str, float] = {}
    all_snapshots: dict[str, ListingSnapshot] = {}
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            last_html = await _click_page_number(page, page_no)

        await _human_linger_on_result_page(page, page_no)
        last_html = await page.get_content()
        await _dump(page, f"ke_area_page_{page_no}")

        page_records = parsers.parse_listing_records(last_html)
        page_snapshots = parsers.parse_listing_snapshots(last_html)
        for house_id, price in page_records:
            all_records[house_id] = price
        for snapshot in page_snapshots:
            all_snapshots[snapshot.house_id] = snapshot

    return all_records, all_snapshots, last_html


async def _wait_for_new_tab(browser, old_tab_ids: set[int], expected_url: Optional[str]):
    for _ in range(20):
        await asyncio.sleep(0.5)
        for tab in browser.tabs:
            if id(tab) not in old_tab_ids:
                return tab
            if expected_url and (tab.target.url or "").startswith(expected_url):
                return tab
    return None


async def _click_detail_link(browser, page, expected_url: Optional[str]):
    old_tab_ids = {id(tab) for tab in browser.tabs}
    try:
        detail_link = await page.select("a.agentCardResblockLink", timeout=4)
    except Exception:
        detail_link = None

    if not detail_link:
        try:
            detail_link = await page.find("查看小区详情", timeout=3)
        except Exception:
            detail_link = None

    if not detail_link:
        return False, None

    if not await _human_click(page, detail_link, "detail link"):
        return False, None

    detail_tab = await _wait_for_new_tab(browser, old_tab_ids, expected_url)
    if detail_tab:
        return True, detail_tab

    current_url = page.target.url or ""
    if expected_url and current_url.startswith(expected_url):
        return True, page

    return True, None


async def _apply_area_filter_on_result_page(page, community_name: str, segment_code: str):
    expected_href = f"/ershoufang/{segment_code}rs{community_name}"
    selectors = [
        f"a[href='{expected_href}']",
        f"a[href^='/ershoufang/{segment_code}rs']",
    ]

    for selector in selectors:
        try:
            element = await page.select(selector, timeout=2)
        except Exception:
            element = None
        if element and await _human_click(page, element, f"result area segment {segment_code}"):
            html = await _wait_for_results_loaded(page, expected_page=1)
            return html

    raise RuntimeError(f"结果页未找到可点击的面积筛选项: {segment_code}")


async def _search_community(page, community_name: str) -> tuple[str, str, Optional[str]]:
    inp = await _get_search_input(page)
    if not await _human_click(page, inp, "search input"):
        raise RuntimeError("搜索框未能成功点击")

    try:
        await inp.clear_input()
    except Exception:
        await inp.send_keys("\uE009a")
        await inp.send_keys("\uE017")
        await page

    await asyncio.sleep(0.5)
    await inp.send_keys(community_name)
    await page
    await asyncio.sleep(1.0)
    await _submit_search(page, inp)

    html = await _wait_for_results_loaded(page, expected_page=1)
    current_url = page.target.url or ""
    detail_url = parsers.find_detail_link(html)
    return html, current_url, detail_url


async def collect(
    browser,
    main_page,
    community_name: str,
    area_min: float,
    area_max: float,
    request_id: Optional[str] = None,
) -> PlatformResult:
    start = time.time()
    log.info("[4] 收到请求: 小区=%s 面积=%.0f~%.0f㎡", community_name, area_min, area_max)
    try:
        return await _do_collect(
            browser=browser,
            main_page=main_page,
            community_name=community_name,
            area_min=area_min,
            area_max=area_max,
            request_id=request_id,
            started_at=start,
        )
    except Exception as exc:
        log.exception("采集异常")
        return PlatformResult(
            name="贝壳",
            status="ERROR",
            reason=str(exc),
            request_id=request_id,
            elapsed_seconds=round(time.time() - start, 2),
        )


async def _do_collect(
    *,
    browser,
    main_page,
    community_name: str,
    area_min: float,
    area_max: float,
    request_id: Optional[str],
    started_at: float,
) -> PlatformResult:
    log.info("[5] 刷新页面（保活插口）")
    main_page = await _reset_to_start_page(main_page)
    await _get_search_input(main_page)
    await _dump(main_page, "ke_refresh")

    keyword_html, keyword_url, detail_url = await _search_community(main_page, community_name)
    await _dump(main_page, "ke_keyword_result")

    if _is_login_url(keyword_url) or _is_login_html(keyword_html):
        status = "WAIT_MANUAL_VERIFY" if _is_manual_verify_html(keyword_html) else "LOGIN_EXPIRED"
        return PlatformResult(
            name="贝壳",
            status=status,
            reason="搜索后进入登录或验证页面",
            request_id=request_id,
            detail_url=detail_url,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    if not detail_url:
        return PlatformResult(
            name="贝壳",
            status="NO_DATA",
            reason="关键词结果页未找到小区详情链接",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    segment_codes = _build_segment_candidates(area_min, area_max)
    all_listing_prices: list[float] = []
    all_listing_snapshots: dict[str, ListingSnapshot] = {}
    last_page_html = keyword_html

    for index, segment_code in enumerate(segment_codes):
        log.info("[6-9] 搜索结果页点击面积档位: %s", segment_code)
        filtered_html = await _apply_area_filter_on_result_page(main_page, community_name, segment_code)
        await _dump(main_page, f"ke_after_area_{segment_code}")

        filtered_url = main_page.target.url or ""
        if _is_login_url(filtered_url) or _is_login_html(filtered_html):
            status = "WAIT_MANUAL_VERIFY" if _is_manual_verify_html(filtered_html) else "LOGIN_EXPIRED"
            return PlatformResult(
                name="贝壳",
                status=status,
                reason=f"面积筛选 {segment_code} 后进入登录或验证页面",
                request_id=request_id,
                detail_url=detail_url,
                elapsed_seconds=round(time.time() - started_at, 2),
            )

        total_pages = _parse_total_pages(filtered_html)
        listing_map, listing_snapshots, last_page_html = await _collect_listing_pages(
            main_page,
            filtered_html,
            total_pages,
        )
        all_listing_prices.extend(list(listing_map.values()))
        all_listing_snapshots.update(listing_snapshots)

        if index < len(segment_codes) - 1:
            keyword_html, keyword_url, detail_url = await _search_community(main_page, community_name)
            await _dump(main_page, f"ke_keyword_result_{segment_code}_return")

    if not all_listing_prices:
        return PlatformResult(
            name="贝壳",
            status="NO_DATA",
            reason="面积结果页未抓到在售单价",
            request_id=request_id,
            detail_url=detail_url,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    detail_url = parsers.find_detail_link(last_page_html) or detail_url
    xiaoqu_id = _extract_xiaoqu_id(detail_url)
    log.info("[10] 小区详情: xiaoqu_id=%s", xiaoqu_id)

    detail_clicked, detail_tab = await _click_detail_link(browser, main_page, detail_url)
    if not detail_clicked or detail_tab is None:
        return PlatformResult(
            name="贝壳",
            status="ERROR",
            reason="未能成功打开小区详情页",
            request_id=request_id,
            detail_url=detail_url,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    await detail_tab
    await asyncio.sleep(3)
    detail_html = await detail_tab.get_content()
    await _dump(detail_tab, "ke_detail")

    community_avg_price = parsers.parse_community_avg_price(detail_html)
    deal_records = parsers.parse_deal_records(detail_html)
    filtered_deal_prices = parsers.filter_deal_prices_by_area(
        deal_records,
        area_min,
        area_max,
    )
    log.info("[11] 小区均价=%s 成交单价=%d条", community_avg_price, len(filtered_deal_prices))

    if detail_tab is not main_page:
        asyncio.ensure_future(_close_tab_later(detail_tab))
        try:
            await main_page.activate()
            await main_page
            log.info("[14] switched back to main tab, detail tab will close later")
        except Exception as exc:
            log.warning("[14] failed to switch back to main tab: %s", exc)

    return PlatformResult(
        name="贝壳",
        status="SUCCESS",
        community_avg_price=community_avg_price,
        quote_prices=all_listing_prices,
        deal_prices=filtered_deal_prices,
        request_id=request_id,
        detail_url=detail_url,
        elapsed_seconds=round(time.time() - started_at, 2),
        listing_snapshots=list(all_listing_snapshots.values()),
    )


async def _close_tab_later(tab):
    """详情页停留 60s 后关闭。"""
    try:
        log.info("[15] 详情页停留 %ds 后关闭", config.DETAIL_TAB_LINGER_SECONDS)
        await asyncio.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        await tab.close()
        log.info("[15] 详情页已关闭")
    except Exception as exc:
        log.warning("[15] 关闭异常: %s", exc)
