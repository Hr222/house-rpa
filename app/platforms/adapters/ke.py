# -*- coding: utf-8 -*-
"""贝壳平台采集适配逻辑。"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Optional

from app.core import config
from app.parsers import ke as parsers
from app.utils.debug_utils import dump_html
from app.core.models import ListingSnapshot, PlatformResult
from app.platforms.base import (
    wait_and_reload_after_block,
    human_linger,
    _human_click,
    click_area_segment,
    safe_select_and_click,
    short_circuit_result,
    community_name_match,
)
from app.platforms.ke_constants import AREA_SEGMENTS, START_URL
from app.platforms.city_map import get_start_url

log = logging.getLogger(__name__)


async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    """真人操作间隔。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


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
    # 翻页后 DOM 树重建，使用 page.find 替代 page.select 以自动重试
    await page.find("ul.sellListContent", timeout=15)
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


async def _click_page_number(page, page_no: int) -> Optional[str]:
    """点击在售页码，返回加载完成后的 HTML；无法翻页时返回 None（优雅停止信号）。

    核心逻辑（找不到按钮→风控检测→恢复重试→点击）由 base.safe_select_and_click 统一处理，
    本函数只保留贝壳特有的点击后等待逻辑（_wait_for_results_loaded）。
    """
    selector = f".house-lst-page-box a[data-page='{page_no}']"
    element = await safe_select_and_click(
        page, selector,
        dump_fn=_dump,
        dump_name=f"ke_page_{page_no}_no_button",
        detect_fn=detect_block,
        block_label=f"第 {page_no} 页(翻页前-按钮缺失)",
        click_label=f"page {page_no}",
    )
    if element is None:
        return None
    return await _wait_for_results_loaded(page, expected_page=page_no)


async def _recover_community_filter(page, community_name: str) -> bool:
    """风控恢复后重新锁定小区：点击结果页顶部小区筛选 dl 里的目标小区项。

    贝壳/链家搜索结果页顶部有小区筛选 dl（<dt title="...小区在售二手房">），
    风控跳转可能把 URL 的小区限定（rs小区名）冲掉，继续翻页会采到别的小区。
    本函数在 dl 里找 .name 匹配目标小区的 a 标签并点击，重新锁定小区。

    Returns: True 表示成功点击并重新锁定；False 表示 dl 不在或没匹配项（调用方应停止翻页保数据正确）。
    """
    # 用 JS 收集小区筛选 dl 内所有候选 {name, href}，Python 侧匹配后按 href 点击对应 a
    js = """
    (() => {
        const dt = document.querySelector('dt[title*="小区在售二手房"]');
        if (!dt) return JSON.stringify({ok: false, reason: 'NO_DL'});
        const dl = dt.closest('dl');
        if (!dl) return JSON.stringify({ok: false, reason: 'NO_DL'});
        const items = [];
        dl.querySelectorAll('a').forEach(a => {
            const nameSpan = a.querySelector('.name');
            if (nameSpan) items.push({name: nameSpan.textContent.trim(), href: a.getAttribute('href') || ''});
        });
        return JSON.stringify({ok: true, items: items});
    })()
    """
    try:
        raw = await page.evaluate(js, return_by_value=True)
    except Exception as exc:
        log.warning("重新锁定小区时读筛选区失败: %s", exc)
        return False

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    if not data.get("ok"):
        log.info("小区筛选 dl 不存在或为空，无法重新锁定")
        return False

    items = data.get("items") or []
    # 用 community_name_match 找匹配目标小区的项
    target = None
    for it in items:
        if community_name_match(community_name, it.get("name", "")):
            target = it
            break
    if target is None:
        log.info("小区筛选 dl 候选 %s 与目标 %r 不匹配，不点击", items, community_name)
        return False

    # 按目标 href 定位 a 并点击（href 形如 /ershoufang/c{id}rs{小区}/）
    href = target.get("href", "")
    el = None
    # 优先用小区 id 段（c{数字}rs）定位，唯一且不受 URL 编码影响
    cid_match = re.search(r"/c(\d+)rs", href)
    locator = f"/c{cid_match.group(1)}rs" if cid_match else href[-40:]
    if locator:
        try:
            el = await page.select(f'a[href*="{locator}"]', timeout=3)
        except Exception:
            el = None
    if el is None:
        # 兜底：用文本找 a
        try:
            el = await page.find(target.get("name", ""), timeout=3)
        except Exception:
            el = None
    if el is None:
        log.warning("匹配到筛选项 %r 但定位不到可点击元素", target)
        return False

    clicked = await _human_click(page, el, f"重新锁定小区 {target.get('name')}")
    if clicked:
        await page
        await asyncio.sleep(3)
        log.info("已点击重新锁定小区: %s", target.get("name"))
        return True
    return False


async def _collect_listing_pages(page, first_page_html: str, total_pages: int, community_name: str = ""):
    all_records: dict[str, float] = {}
    all_snapshots: dict[str, ListingSnapshot] = {}
    last_html = first_page_html

    # 注意：用 while 而非 for range。风控后重新锁定小区会把浏览器带回第 1 页，
    # 必须把 page_no 重置成 1 才能从第 1 页重新翻起；for range 的循环变量赋值会被
    # 下一轮覆盖，无法重置，故显式 while + 末尾 page_no += 1。
    page_no = 1
    while page_no <= total_pages:
        if page_no > 1:
            # 翻页前风控预检（human_linger 停留期间可能被风控，DOM 被替换成验证码页）
            await wait_and_reload_after_block(page, detect_block, f"第 {page_no} 页(翻页前)")

            last_html = await _click_page_number(page, page_no)
            if last_html is None:
                log.warning("第 %d 页无法翻页，停止翻页保数据正确", page_no)
                break

            # 翻页后风控兜底（检测→等人回车→重取，最多2次；不重新点页码避免再触发验证码）
            last_html = await wait_and_reload_after_block(page, detect_block, f"第 {page_no} 页")

            # 风控恢复后验证小区限定是否还在（风控跳转可能冲掉查询条件）
            if community_name:
                page_snaps = parsers.parse_listing_snapshots(last_html)
                if page_snaps and not any(
                    community_name_match(community_name, s.community_name or "")
                    for s in page_snaps
                ):
                    # 限定丢失，尝试点小区筛选 dl 重新锁定
                    if await _recover_community_filter(page, community_name):
                        log.info("第 %d 页风控后重新锁定小区 %s", page_no, community_name)
                        last_html = await wait_and_reload_after_block(page, detect_block, f"第 {page_no} 页(重新锁定后)")
                        # 重新锁定会让浏览器回到该小区第 1 页，重置 page_no 从第 1 页重新翻起，
                        # 否则当轮会把第 1 页当成本页采入、下一轮又跳 page_no+1，数据错位。
                        page_no = 1
                    else:
                        log.warning("第 %d 页风控后小区限定丢失且无法恢复，停止翻页保数据正确", page_no)
                        break

        await human_linger(page, page_no)
        last_html = await page.get_content()
        await _dump(page, f"ke_area_page_{page_no}")

        page_records = parsers.parse_listing_records(last_html)
        page_snapshots = parsers.parse_listing_snapshots(last_html)
        for house_id, price in page_records:
            all_records[house_id] = price
        for snapshot in page_snapshots:
            all_snapshots[snapshot.house_id] = snapshot

        page_no += 1

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


async def _apply_area_filter(page, area_min, area_max):
    """贝壳面积筛选：智能展开 → 面积区"更多及自定义" → 填值 → 确定。

    首页：筛选区已展开；搜索结果页：需先点"更多选项"全局展开。
    """
    # 1. 智能全局展开
    try:
        more_btn = await page.select("div.more.btn-more", timeout=3)
    except Exception:
        more_btn = None
    if more_btn:
        try:
            btn_text = await more_btn.apply("(el) => el.textContent.trim()")
        except Exception:
            btn_text = ""
        if "更多选项" in (btn_text or ""):
            await _human_click(page, more_btn, "global btn-more")
            await page
            await asyncio.sleep(1.5)

    # 2. 在所有 dl.hide.hasmore 中找到 dt[title*="建筑面积"] 的那个
    try:
        containers = await page.select_all("dl.hide.hasmore", timeout=3)
    except Exception:
        containers = []

    area_container = None
    for c in containers:
        try:
            tit = await c.apply(
                "(el) => { const t = el.querySelector('dt'); return t ? t.title || '' : ''; }"
            )
        except Exception:
            tit = ""
        if "建筑面积" in tit:
            area_container = c
            break

    if area_container is None:
        raise RuntimeError("未找到建筑面积筛选区")

    try:
        btns = await area_container.query_selector_all("span.btn-showmore")
    except Exception:
        btns = []
    if btns:
        await _human_click(page, btns[0], "btn-showmore")
        await page
        await asyncio.sleep(1.5)

    try:
        custom = await area_container.query_selector_all("span.customFilter[data-role='area']")
    except Exception:
        custom = []
    if not custom:
        raise RuntimeError("未找到面积自定义输入区")

    min_el = max_el = None
    try:
        m = await custom[0].query_selector_all("input[role='minValue']")
        min_el = m[0] if m else None
    except Exception:
        min_el = None
    try:
        m = await custom[0].query_selector_all("input[role='maxValue']")
        max_el = m[0] if m else None
    except Exception:
        max_el = None
    if min_el is None or max_el is None:
        raise RuntimeError("未找到面积自定义输入框")

    await _human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)

    await _human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)

    try:
        btns = await custom[0].query_selector_all("button.btn-range")
    except Exception:
        btns = []
    if not btns:
        raise RuntimeError("未找到面积确定按钮")

    try:
        await _human_click(page, btns[0], "area confirm")
    except Exception:
        pass
    else:
        await page
        await asyncio.sleep(3)
        return

    # JS 兜底
    await page.evaluate(
        """
        (() => {
            const btn = document.querySelector('.customFilter[data-role=\"area\"] .btn-range');
            if (btn) { btn.classList.remove('hide'); btn.click(); return true; }
            return false;
        })()
        """,
        return_by_value=True,
    )
    await page
    await asyncio.sleep(3)


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
    area: float,
    request_id: Optional[str] = None,
    city: str = "深圳",
) -> PlatformResult:
    start = time.time()
    log.info("[4] 收到请求: 小区=%s 面积=%.1f㎡ 城市=%s", community_name, area, city)
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
    request_id: Optional[str],
    started_at: float,
    area: float,
    city: str = "深圳",
) -> PlatformResult:
    log.info("[5] 刷新页面（保活插口）")
    main_page = await _reset_to_start_page(main_page, city)
    # 采集起点风控兜底：首页若被风控(CAPTCHA/登录失效)，阻塞等人解除后重取，
    # 避免带着 CAPTCHA 往下走导致静默 NO_DATA
    await wait_and_reload_after_block(main_page, detect_block, "首页")
    await _get_search_input(main_page)
    await _dump(main_page, "ke_refresh")

    keyword_html, keyword_url, detail_url = await _search_community(main_page, community_name)
    # 搜索后风控兜底（搜索是最易触发风控的环节，带关键词请求）
    keyword_html = await wait_and_reload_after_block(main_page, detect_block, "搜索后")
    keyword_url = main_page.target.url or ""
    detail_url = parsers.find_detail_link(keyword_html) or detail_url
    await _dump(main_page, "ke_keyword_result")

    if _is_login_url(keyword_url) or _is_login_html(keyword_html):
        status = "WAIT_MANUAL_VERIFY" if _is_manual_verify_html(keyword_html) else "LOGIN_EXPIRED"
        return short_circuit_result(
            "贝壳", status, "搜索后进入登录或验证页面",
            request_id, started_at, detail_url=detail_url,
        )

    if not detail_url:
        return short_circuit_result(
            "贝壳", "NO_DATA", "关键词结果页未找到小区详情链接",
            request_id, started_at,
        )

    # 无数据短路：贝壳"暂无房源"页面（仍有小区详情链接，需靠 m-noresult 识别）
    if "m-noresult" in keyword_html:
        return short_circuit_result(
            "贝壳", "NO_DATA", "小区暂无在售房源",
            request_id, started_at, detail_url=detail_url,
        )

    # 校验搜索结果是否真的属于目标小区（解析 listing 的社区名，不用 raw HTML 切片）
    keyword_snaps = parsers.parse_listing_snapshots(keyword_html)
    if not any(community_name_match(community_name, s.community_name or "") for s in keyword_snaps):
        return short_circuit_result(
            "贝壳", "NO_DATA", f"关键词搜索未匹配到小区: {community_name}",
            request_id, started_at, detail_url=detail_url,
        )

    # 4. 面积筛选（动态读取页面档位，点击对应区间链接；返回区间用于成交筛选）
    area_range = await click_area_segment(main_page, area, parsers.parse_area_segments, "ke")
    if area_range is None:
        return short_circuit_result(
            "贝壳", "NO_DATA", "该面积区间无在售房源（档位已禁用）",
            request_id, started_at, detail_url=detail_url,
        )
    area_min, area_max = area_range
    log.info("[4] 面积筛选区间: %.0f~%.0f (来自档位匹配)", area_min, area_max)
    await _dump(main_page, "ke_after_area")

    all_listing_prices: list[float] = []
    all_listing_snapshots: dict[str, ListingSnapshot] = {}

    filtered_html = await _wait_for_results_loaded(main_page, expected_page=1)
    # 面积筛选后风控兜底（对齐 lj，面积筛选点击也可能触发风控）
    filtered_html = await wait_and_reload_after_block(main_page, detect_block, "面积筛选后")
    filtered_url = main_page.target.url or ""
    if _is_login_url(filtered_url) or _is_login_html(filtered_html):
        status = "WAIT_MANUAL_VERIFY" if _is_manual_verify_html(filtered_html) else "LOGIN_EXPIRED"
        return short_circuit_result(
            "贝壳", status, "面积筛选后进入登录或验证页面",
            request_id, started_at, detail_url=detail_url,
        )

    total_pages = _parse_total_pages(filtered_html)
    listing_map, listing_snapshots, last_page_html = await _collect_listing_pages(
        main_page,
        filtered_html,
        total_pages,
        community_name,
    )
    all_listing_prices.extend(list(listing_map.values()))
    all_listing_snapshots.update(listing_snapshots)

    if not all_listing_prices:
        return short_circuit_result(
            "贝壳", "NO_DATA", "面积结果页未抓到在售单价",
            request_id, started_at, detail_url=detail_url,
        )

    detail_url = parsers.find_detail_link(last_page_html) or detail_url
    xiaoqu_id = _extract_xiaoqu_id(detail_url)
    log.info("[10] 小区详情: xiaoqu_id=%s", xiaoqu_id)

    detail_clicked, detail_tab = await _click_detail_link(browser, main_page, detail_url)
    if not detail_clicked or detail_tab is None:
        # 详情 tab 未打开：不整单失败，降级为仅用在售均价（走 QUOTE_DISCOUNT），
        # 避免浪费已采到的在售数据
        log.warning("[10] 未能打开小区详情页，降级为仅用在售均价")
        return PlatformResult(
            name="贝壳",
            status="SUCCESS",
            community_avg_price=None,
            quote_prices=all_listing_prices,
            deal_prices=[],
            deal_records=[],
            deal_source="无成交(详情页未取得)",
            request_id=request_id,
            detail_url=detail_url,
            elapsed_seconds=round(time.time() - started_at, 2),
            listing_snapshots=list(all_listing_snapshots.values()),
        )

    # 详情页风控兜底（检测→等人回车→重取，最多 2 次）
    detail_html = await wait_and_reload_after_block(detail_tab, detect_block, "详情页")
    await _dump(detail_tab, "ke_detail")

    community_avg_price = parsers.parse_community_avg_price(detail_html)
    deal_records = parsers.parse_deal_records(detail_html)
    filtered_deal_prices = parsers.filter_deal_prices_by_area(
        deal_records,
        area_min,
        area_max,
    )
    log.info("[11] 小区均价=%s 成交单价=%d条", community_avg_price, len(filtered_deal_prices))

    # 详情页经 2 次人工仍未取得任何成交/均价：同样降级，不整单失败
    if community_avg_price is None and not filtered_deal_prices:
        log.warning("[11] 详情页未抓到均价和成交，降级为仅用在售均价")
        if detail_tab is not main_page:
            asyncio.ensure_future(_close_tab_later(detail_tab))
        return PlatformResult(
            name="贝壳",
            status="SUCCESS",
            community_avg_price=None,
            quote_prices=all_listing_prices,
            deal_prices=[],
            deal_records=[],
            deal_source="无成交(详情页未取得)",
            request_id=request_id,
            detail_url=detail_url,
            elapsed_seconds=round(time.time() - started_at, 2),
            listing_snapshots=list(all_listing_snapshots.values()),
        )

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
        deal_records=[
            {"area": r.area, "price": r.unit_price}
            for r in deal_records if r.unit_price is not None
        ][:10],
        deal_source="成交记录" if filtered_deal_prices else "无成交",
        request_id=request_id,
        detail_url=detail_url,
        elapsed_seconds=round(time.time() - started_at, 2),
        listing_snapshots=list(all_listing_snapshots.values()),
    )


async def _close_tab_later(tab):
    """详情页停留后关闭。"""
    try:
        await asyncio.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        await tab.close()
    except Exception as exc:
        log.warning("关闭详情标签异常: %s", exc)
