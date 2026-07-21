# -*- coding: utf-8 -*-
"""房天下平台采集适配逻辑。

业务流程与贝壳一致（搜索→筛选→抓在售→点详情→抓成交→算最终价），
但因平台特性有以下差异：
- 面积筛选：自定义输入框填值（贝壳是点预设档位 a1-a7）
- 有分页：需翻页采集（贝壳也要翻页；安居客无分页）
- 详情入口只在第一页有：分页前先 Ctrl+点击打开详情（后台新标签），
  采完在售再切到详情标签抓成交（方案B）。
- 成交记录在详情页的"小区成交"tab，同页跳转到 /loupan/{id}/chengjiao/。
- 成交筛选规则：严格面积区间（不套容差）+ 近半年。
  注意：贝壳用的是 ±20% 容差（parsers.filter_deal_prices_by_area），
  房天下按业务确认用严格区间，两者口径不同，各自实现，不混用。

采集逻辑移植自 fang_mvp_test.py 全链路验证通过的实现。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional

from app.core import config
from app.utils.debug_utils import dump_html
from app.core.models import ListingSnapshot, PlatformResult
from app.parsers import fang as parsers
from app.platforms.fang_constants import START_URL
from app.platforms.city_map import get_start_url
from app.platforms.base import (
    wait_and_reload_after_block,
    human_linger,
    _human_click,
    click_area_segment,
    short_circuit_result,
    has_matching_community_snapshots,
    filter_snapshots_by_community,
    prepare_listing_data,
    check_empty_listing_page,
)

log = logging.getLogger(__name__)


# ============================================================
# 风控 / 登录判定
# ============================================================

def _is_captcha_url(url: str) -> bool:
    """房天下验证码拦截页 URL 特征（具体待 dump 核对后收敛）。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def _is_captcha_html(html: str) -> bool:
    markers = (
        "请输入验证码",
        "验证后继续访问",
        "请完成验证",
        "滑动验证",
    )
    return any(marker in html for marker in markers)


def _is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url or "signin" in url


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """房天下风控/登录检测。"""
    if _is_captcha_url(url) or _is_captcha_html(html or ""):
        return True, "命中验证码拦截"
    if _is_login_url(url):
        return True, "命中登录页"
    return False, ""


# ============================================================
# 页面交互辅助
# ============================================================

async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    """真人操作间隔。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _dump(page, name: str):
    """调试模式下导出页面 HTML。"""
    await dump_html(page, name, logger=log)


async def _is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def _get_search_input(page):
    """定位房天下搜索框。

    DOM: <input id="input_keyw1" name="input_keyw1" placeholder="请输入小区名称、学校名称…">
    """
    selectors = [
        "input#input_keyw1",
        "input[name='input_keyw1']",
        "input[placeholder*='小区']",
        "input[placeholder*='学校']",
    ]
    for selector in selectors:
        try:
            elements = await page.select_all(selector, timeout=1.5)
        except Exception:
            continue
        for element in elements:
            if await _is_interactable(element):
                return selector, element
        if elements:
            return selector, elements[0]
    return None, None


async def _get_search_submit(page):
    """定位房天下搜索按钮。

    DOM: <input type="submit" id="kesfqbfylb_A01_11_02" value="搜 索" class="btn">
    """
    selectors = [
        "input#kesfqbfylb_A01_11_02",
        "input[type='submit'].btn",
        "input[type='submit'][value*='搜']",
    ]
    for selector in selectors:
        try:
            elements = await page.select_all(selector, timeout=1.5)
        except Exception:
            continue
        for element in elements:
            if await _is_interactable(element):
                return selector, element
        if elements:
            return selector, elements[0]
    return None, None


async def _fill_area_inputs(page, area_min, area_max):
    """定位面积筛选输入框并填值，返回是否点击确定成功。

    房天下 DOM（id 重复，取第一组）：
      <li class="text_inp" name="customarea">
        <input id="cminArea" ...> - <input id="cmaxArea" ...>
        <input id="aConfirmButton" type="button" value="确定" style="display:none;">
      </li>
    """
    try:
        container = await page.select("li[name='customarea']", timeout=3)
    except Exception:
        container = None
    if container is None:
        raise RuntimeError("未找到面积筛选区（li[name='customarea']）")

    min_el = await container.query_selector_all("input#cminArea")
    max_el = await container.query_selector_all("input#cmaxArea")
    if not min_el or not max_el:
        raise RuntimeError("面积筛选区未找到 cminArea/cmaxArea")
    min_el, max_el = min_el[0], max_el[0]

    # 填下限
    await _human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)

    # 填上限
    await _human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)

    # 点"确定"提交
    confirms = await container.query_selector_all("#aConfirmButton")
    confirm_clicked = False
    if confirms:
        confirm_clicked = await _human_click(page, confirms[0], "area confirm")
    if not confirm_clicked:
        try:
            await max_el.send_keys("\r")
            await page
            confirm_clicked = True
        except Exception:
            pass

    await page
    await asyncio.sleep(3)
    return confirm_clicked


# ============================================================
# 分页采集（房天下有分页，参考贝壳分页写法；解析在 parsers/fang.py）
# ============================================================

async def _wait_for_results_loaded(page, expected_page: Optional[int] = None, timeout: float = 15) -> str:
    """等待结果页加载完成。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last_html = ""
    while asyncio.get_event_loop().time() < deadline:
        last_html = await page.get_content()
        if expected_page is None or parsers.parse_current_page(last_html) == expected_page:
            await asyncio.sleep(1.2)
            return last_html
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.2)
    return last_html or await page.get_content()


async def _click_page_number(page, page_no: int) -> None:
    """点击房天下页码按钮并等待加载完成。

    DOM: <div class="page_box"><div class="page_al"><span><a href="...">2</a></span>
    页码文本即页号，靠文本定位。
    """
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
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")

    if not await _human_click(page, target, f"page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")

    await _wait_for_results_loaded(page, expected_page=page_no)



async def _collect_listing_pages(page, total_pages: int, community_name: str = ""):
    """逐页采集并只累计匹配目标小区的在售房源。

    每页单独截断到"您可能感兴趣的新房"(InterestedNewHouse)之前再解析。
    第 2 页起若整页无目标小区房源，立即停止后续翻页。
    """

    def _cut_main(html_text: str) -> str:
        cut = html_text.find("InterestedNewHouse")
        return html_text[:cut] if cut > 0 else html_text

    all_snapshots: list[ListingSnapshot] = []
    page_counts: list[tuple[int, int]] = []
    consecutive_empty = 0

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            try:
                await _click_page_number(page, page_no)
            except RuntimeError:
                log.warning("第 %d 页页码按钮未找到，停止翻页", page_no)
                break

            # 翻页后风控兜底（检测→等人回车→重取，最多 2 次；不重新点页码避免再触发验证码）
            await wait_and_reload_after_block(page, detect_block, f"第 {page_no} 页")

        await human_linger(page, page_no)
        page_html = await page.get_content()
        await _dump(page, f"fang_area_page_{page_no}")

        page_snapshots = parsers.parse_listing_snapshots(_cut_main(page_html))
        matched_snapshots = filter_snapshots_by_community(page_snapshots, community_name)
        count = len(page_snapshots)
        page_counts.append((page_no, count))
        log.info(
            "房天下第 %d/%d 页在售过滤: 总 %d 条 -> 匹配小区 %s %d 条",
            page_no, total_pages, count, community_name, len(matched_snapshots),
        )

        if page_snapshots and not matched_snapshots:
            if page_no == 1:
                log.warning("第 1 页面积结果全部不属于小区 %s，停止采集", community_name)
            else:
                log.warning("第 %d 页房源全部不属于小区 %s，停止后续翻页", page_no, community_name)
            break

        all_snapshots.extend(matched_snapshots)

        # 空页检测：首页空→error+停止，连续空页≥2→warning+停止
        should_stop, consecutive_empty = check_empty_listing_page(
            page_no, count, consecutive_empty, total_pages, platform="fang")
        if should_stop:
            break

    return all_snapshots, page_counts


# ============================================================
# 详情页（Ctrl+点击后台打开新标签，入口只在第一页有）
# ============================================================

async def _wait_for_new_tab(browser, old_tab_ids: set, expected_url, timeout=20):
    """等待新标签页打开，参考 ke_adapter._wait_for_new_tab。"""
    for _ in range(int(timeout / 0.5)):
        await asyncio.sleep(0.5)
        for tab in browser.tabs:
            if id(tab) not in old_tab_ids:
                return tab
            if expected_url and (tab.target.url or "").startswith(expected_url):
                return tab
    return None


async def _click_detail_link(browser, page):
    """点击"小区详情"打开新标签页，返回 (是否成功, 详情标签页)。

    多浏览器模式下直接用 human_click，每个平台独立浏览器，打开新标签不影响其他平台。
    """
    old_tab_ids = {id(tab) for tab in browser.tabs}

    detail_link = None
    for selector in ("a.href_xq", "a#kesfqbfylb_A01_08_01", "a[href*='/loupan/']"):
        try:
            detail_link = await page.select(selector, timeout=4)
        except Exception:
            detail_link = None
        if detail_link:
            break

    if not detail_link:
        try:
            detail_link = await page.find("小区详情", timeout=3)
        except Exception:
            detail_link = None

    if not detail_link:
        return False, None

    if not await _human_click(page, detail_link, "detail link"):
        return False, None

    detail_tab = await _wait_for_new_tab(browser, old_tab_ids, "/loupan/")
    return detail_tab is not None, detail_tab


async def _click_deal_tab(detail_tab):
    """点详情页的"小区成交"tab（同页跳转到 /loupan/{id}/chengjiao/）。

    注意：详情页顶部导航有"查成交"(全市链接 /chengjiao/)，
    必须精确点"小区成交"(含 /loupan/ + /chengjiao/)，否则会跳到全市成交页。
    """
    # 优先按文本定位（最稳），排除顶部导航的"查成交"全市链接
    try:
        deal_link = await detail_tab.find("小区成交", timeout=4)
    except Exception:
        deal_link = None
    if deal_link:
        try:
            await deal_link.click()
            await detail_tab
            await asyncio.sleep(2)
            return True
        except Exception:
            pass
    # 兜底：精确选择器，必须同时含 /loupan/ 和 /chengjiao/
    for selector in ("a[href*='/loupan/'][href*='/chengjiao/']",):
        try:
            deal_link = await detail_tab.select(selector, timeout=4)
        except Exception:
            deal_link = None
        if deal_link:
            try:
                await deal_link.click()
                await detail_tab
                await asyncio.sleep(2)
                return True
            except Exception:
                pass
    return False


# ============================================================
# ============================================================
# 成交页导航与解析（后台并行任务）
# ============================================================

async def _navigate_and_parse_deals(detail_tab, main_page, area_min: float, area_max: float) -> tuple[list, list]:
    """在 detail_tab 上导航到成交页并解析（与分页并行）。

    解析完后立刻关闭 detail_tab 并切回 main_page，避免下一页单被卡住。
    """
    deal_prices: list = []
    deal_record_dicts: list = []
    try:
        await detail_tab
        await _dump(detail_tab, "fang_detail")
        detail_html = await detail_tab.get_content()
        deal_url = parsers.find_deal_link(detail_html)
        if deal_url:
            log.info("导航到成交页: %s", deal_url)
            await detail_tab.get(deal_url)
            await detail_tab
            log.info("成交页当前 URL: %s", detail_tab.target.url)
        else:
            log.warning("详情页未找到成交页链接")
            return deal_prices, deal_record_dicts

        await _dump(detail_tab, "fang_deal")
        # 成交页风控兜底（检测→等人回车→重取，最多 2 次）
        deal_html = await wait_and_reload_after_block(detail_tab, detect_block, "成交页")

        all_deals = parsers.parse_deal_records(deal_html)
        filtered_deals = parsers.filter_deal_records(all_deals, area_min, area_max, months=6)
        deal_prices = [d[3] for d in filtered_deals]
        deal_record_dicts = [
            {"area": d[0], "date": d[1], "price": d[3]}
            for d in filtered_deals if d[3] is not None
        ]
        log.info(
            "成交记录: 总 %d 条, %.0f-%.0f㎡且近半年 %d 条",
            len(all_deals), area_min, area_max, len(filtered_deals),
        )
    except Exception as exc:
        log.warning("成交页导航/解析失败: %s", exc)
    finally:
        # 解析完立刻切回主页，tab 等 _close_tab_later 自动关
        try:
            await main_page.activate()
        except Exception as exc:
            log.warning("切回主页面失败: %s", exc)
    return deal_prices, deal_record_dicts


# ============================================================
# 搜索
# ============================================================

async def _search_community(page, community_name: str) -> str:
    """搜索小区，返回结果页 HTML。"""
    _, inp = await _get_search_input(page)
    if inp is None:
        raise RuntimeError("未找到房天下搜索框")

    if not await _human_click(page, inp, "search input"):
        raise RuntimeError("搜索框未能成功点击")

    try:
        await inp.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.5)
    await inp.send_keys(community_name)
    await page
    await asyncio.sleep(1.0)

    # 优先点搜索按钮；失败回车兜底
    submitted = False
    _, submit_btn = await _get_search_submit(page)
    if submit_btn and await _human_click(page, submit_btn, "search submit"):
        await page
        await asyncio.sleep(3)
        submitted = True

    if not submitted:
        try:
            await inp.send_keys("\r")
            await page
            await asyncio.sleep(3)
            submitted = True
        except Exception:
            pass

    if not submitted:
        raise RuntimeError("未能提交搜索")

    return await page.get_content()


# ============================================================
# 页面复位
# ============================================================

async def _close_tab_later(tab):
    """详情页停留后关闭。"""
    try:
        await asyncio.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        await tab.close()
    except Exception as exc:
        log.warning("关闭详情标签异常: %s", exc)


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
    """检查当前页是否已登录、未被风控、且能执行搜索。"""
    try:
        await main_page.select("body", timeout=10)
        await main_page
        html = await main_page.get_content()
        current_url = main_page.target.url or ""
    except Exception as exc:
        return False, f"页面不可用: {exc}"

    if _is_captcha_url(current_url) or _is_captcha_html(html):
        return False, "命中验证码拦截，等待人工处理"
    if _is_login_url(current_url):
        return False, "当前会话未登录或已失效"

    try:
        _, inp = await _get_search_input(main_page)
        if inp is None:
            return False, "未找到搜索框，页面未就绪"
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
        main_page = await reset_to_start_page(main_page)
    except Exception as exc:
        return False, f"刷新保活失败: {exc}"

    return await probe_ready(main_page)


# ============================================================
# 采集主体
# ============================================================

async def collect(
    browser,
    main_page,
    community_name: str,
    area: float,
    request_id: Optional[str] = None,
    city: str = "深圳",
) -> PlatformResult:
    """执行一次完整的房天下询价采集。"""
    start = time.time()
    log.info("收到请求: 小区=%s 面积=%.0f㎡ 城市=%s", community_name, area, city)
    try:
        return await _do_collect(
            browser=browser,
            main_page=main_page,
            community_name=community_name,
            area=area,
            request_id=request_id,
            started_at=start,
            city=city,
        )
    except Exception as exc:
        log.exception("采集异常")
        return PlatformResult(
            name="房天下",
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
    request_id: Optional[str],
    started_at: float,
    area: float,
    city: str = "深圳",
) -> PlatformResult:
    # 1. 刷新首页保活
    main_page = await reset_to_start_page(main_page, city)
    # 采集起点风控兜底：首页若被风控(CAPTCHA/登录失效)，阻塞等人解除后重取，
    # 避免带着 CAPTCHA 往下走导致静默 NO_DATA
    await wait_and_reload_after_block(main_page, detect_block, "首页")
    await _dump(main_page, "fang_refresh")

    # 3-5. 搜索 + 面积筛选（含重试：面积档位点击后可能丢失小区限定）
    area_range = None
    area_html = ""
    for attempt in (1, 2):
        # 搜索小区
        keyword_html = await _search_community(main_page, community_name)
        await _dump(main_page, "fang_keyword_result")
        keyword_url = main_page.target.url or ""

        # 判风控/登录
        if _is_captcha_url(keyword_url) or _is_captcha_html(keyword_html):
            return short_circuit_result(
                "房天下", "WAIT_MANUAL_VERIFY", "搜索后命中验证码拦截",
                request_id, started_at,
            )
        if _is_login_url(keyword_url):
            return short_circuit_result(
                "房天下", "LOGIN_EXPIRED", "搜索后进入登录页",
                request_id, started_at,
            )

        # 无数据短路：检查结果列表是否存在
        if '<dl class="clearfix' not in keyword_html:
            return short_circuit_result(
                "房天下", "NO_DATA", "关键词搜索无在售房源",
                request_id, started_at,
            )

        # 校验搜索结果是否真的属于目标小区（解析 listing 的社区名，不用 raw HTML）
        keyword_snaps = parsers.parse_listing_snapshots(keyword_html)
        if not has_matching_community_snapshots(keyword_snaps, community_name):
            return short_circuit_result(
                "房天下", "NO_DATA", f"关键词搜索未匹配到小区: {community_name}",
                request_id, started_at,
            )

        # 面积筛选
        area_range = await click_area_segment(main_page, area, parsers.parse_area_segments, "fang")
        await _dump(main_page, "fang_after_area")
        area_url = main_page.target.url or ""
        area_html = await main_page.get_content()
        if _is_captcha_url(area_url) or _is_captcha_html(area_html):
            return short_circuit_result(
                "房天下", "WAIT_MANUAL_VERIFY", "面积筛选后命中验证码拦截",
                request_id, started_at,
            )
        if area_range is None:
            return short_circuit_result(
                "房天下", "NO_DATA", "该面积区间无在售房源（档位已禁用）",
                request_id, started_at,
            )

        # 验证：面积筛选后总页数不应超过筛选前
        keyword_pages = parsers.parse_total_pages(keyword_html)
        area_pages = parsers.parse_total_pages(area_html)
        log.info("总页数: 筛选前=%d → 筛选后=%d", keyword_pages, area_pages)

        if area_pages <= keyword_pages:
            break  # 正常

        if attempt == 1:
            log.warning("面积筛选后页数 %d→%d，小区限定可能丢失，刷新首页重试",
                         keyword_pages, area_pages)
            main_page = await reset_to_start_page(main_page, city)
            continue

        # 两次都失败
        return short_circuit_result(
            "房天下", "ERROR",
            f"面积筛选后小区限定丢失（页数 {keyword_pages}→{area_pages}，重试2次无效）",
            request_id, started_at,
        )

    area_min, area_max = area_range if area_range else (area * 0.8, area * 1.2)
    log.info("[4] 面积筛选区间: %.0f~%.0f (来自档位匹配)", area_min, area_max)

    # 详情入口只在第一页有；先确认面积结果页仍包含目标小区，再打开详情标签。
    first_page_snapshots = parsers.parse_listing_snapshots(area_html)
    if not has_matching_community_snapshots(first_page_snapshots, community_name):
        return short_circuit_result(
            "房天下", "NO_DATA", f"面积筛选后未匹配到小区: {community_name}",
            request_id, started_at,
        )

    # 6. 点开小区详情（Ctrl+点击后台新标签，入口只在第一页有，翻页前必须点）
    log.info("点击小区详情（分页前先点开）")
    detail_clicked, detail_tab = await _click_detail_link(browser, main_page)
    if detail_clicked and detail_tab is not None:
        log.info("详情标签已打开")
    else:
        log.warning("未能打开小区详情页")

    # 6. 并行：分页采集在售房源 + 导航成交页

    # 启动成交页导航任务（后台并行，解析完自动关成交 tab 切回主页）
    deal_prices_future: Optional[asyncio.Task] = None
    deal_record_dicts_future: Optional[asyncio.Task] = None
    if detail_tab is not None:
        deal_prices_future = asyncio.ensure_future(
            _navigate_and_parse_deals(detail_tab, main_page, area_min, area_max)
        )

    # 分页采集在售房源（主线程，和成交导航并行）
    collected_snapshots, page_counts = await _collect_listing_pages(
        main_page, area_pages, community_name
    )
    log.info("分页采集完成: 每页 %s", page_counts)

    # 7. 返回前防御校验，确保在售价格与房源明细来自同一批目标小区数据
    snapshots, quote_prices = prepare_listing_data(collected_snapshots, community_name)
    log.info(
        "房天下在售房源最终校验: 已采集 %d 条 -> 匹配小区 %s %d 条",
        len(collected_snapshots), community_name, len(snapshots),
    )
    if not snapshots:
        return short_circuit_result(
            "房天下", "NO_DATA", f"面积筛选后未匹配到小区: {community_name}",
            request_id, started_at,
        )
    if not quote_prices:
        return short_circuit_result(
            "房天下", "NO_DATA", "面积结果页未抓到在售单价",
            request_id, started_at,
        )
    quote_avg = sum(quote_prices) / len(quote_prices)

    # 8. 等待成交导航完成（已在后台并行，途中已自动关 tab + 切回主页）
    deal_prices = []
    deal_record_dicts = []
    if deal_prices_future is not None:
        deal_prices, deal_record_dicts = await deal_prices_future

    deal_avg = sum(deal_prices) / len(deal_prices) if deal_prices else None

    log.info(
        "在售均价=%.2f 成交均价=%s 在售条数=%d",
        quote_avg,
        f"{deal_avg:.2f}" if deal_avg else "None",
        len(quote_prices),
    )

    # 9. 关掉详情/成交标签（已被并行任务关闭，二次关防御）
    if detail_tab is not None:
        asyncio.ensure_future(_close_tab_later(detail_tab))

    return PlatformResult(
        name="房天下",
        status="SUCCESS",
        community_avg_price=None,
        quote_prices=quote_prices,
        deal_prices=deal_prices,
        deal_records=deal_record_dicts,
        deal_source="成交记录",
        request_id=request_id,
        detail_url=None,
        elapsed_seconds=round(time.time() - started_at, 2),
        listing_snapshots=snapshots,
    )
