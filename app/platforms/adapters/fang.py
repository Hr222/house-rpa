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
import re
import time
from datetime import datetime, timedelta
from typing import Optional

from app.core import config
from app.utils.debug_utils import dump_html
from app.core.models import ListingSnapshot, PlatformResult
from app.platforms.fang_constants import START_URL
from app.platforms.base import human_linger

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


def _is_login_html(html: str) -> bool:
    markers = (
        "请输入手机号",
        "请输入密码",
        "手机快捷登录",
        "扫码登录",
    )
    return any(marker in html for marker in markers)


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """房天下风控/登录检测。"""
    if _is_captcha_url(url) or _is_captcha_html(html or ""):
        return True, "命中验证码拦截"
    if _is_login_html(html or ""):
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


async def _human_click(page, element, label: str) -> bool:
    if not element:
        return False
    try:
        await element.scroll_into_view()
    except Exception:
        pass
    try:
        await element.mouse_move()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    last_error = None
    for clicker in ("js", "mouse"):
        try:
            if clicker == "js":
                await element.click()
            else:
                await element.mouse_click()
            await page
            await asyncio.sleep(0.8)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s click failed: %s", label, last_error)
    return False


# ============================================================
# 面积自定义输入（房天下也是填值+点确定）
# ============================================================

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
# 分页采集（房天下有分页，参考贝壳分页写法）
# ============================================================

def _parse_total_pages(html: str) -> int:
    """从房天下分页区解析总页数。

    DOM: <span class="last">共2页</span>
    """
    m = re.search(r'共(\d+)页', html or "")
    return int(m.group(1)) if m else 1


def _parse_current_page(html: str) -> Optional[int]:
    """从房天下分页区解析当前页码。

    DOM: <span class="on">1</span>（当前页带 class=on）
    """
    m = re.search(r'<span class="on">(\d+)</span>', html or "")
    return int(m.group(1)) if m else None


async def _wait_for_results_loaded(page, expected_page: Optional[int] = None, timeout: float = 15) -> str:
    """等待结果页加载完成。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last_html = ""
    while asyncio.get_event_loop().time() < deadline:
        last_html = await page.get_content()
        if expected_page is None or _parse_current_page(last_html) == expected_page:
            await asyncio.sleep(1.2)
            return last_html
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.2)
    return last_html or await page.get_content()


async def _click_page_number(page, page_no: int) -> str:
    """点击房天下页码按钮，返回加载完成后的 HTML。

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

    return await _wait_for_results_loaded(page, expected_page=page_no)



async def _collect_listing_pages(page, first_page_html: str, total_pages: int, dump_prefix: str = "fang_area_page"):
    """逐页采集，合并所有页的主结果区 HTML 供后续解析。参考贝壳 collect_listing_pages。

    每页单独截断到"您可能感兴趣的新房"(InterestedNewHouse)之前，再拼接。
    如果在合并后的 HTML 上截断，第一页的 InterestedNewHouse 会把第二页全切掉。

    返回 (合并后的主结果区 HTML, 每页记录数, 每页 dump 文件)。
    """

    def _cut_main(html_text: str) -> str:
        cut = html_text.find("InterestedNewHouse")
        return html_text[:cut] if cut > 0 else html_text

    all_html_parts: list[str] = [_cut_main(first_page_html)]
    page_counts: list[tuple[int, int]] = []
    page_files: list[Optional] = []
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            last_html = await _click_page_number(page, page_no)
            all_html_parts.append(_cut_main(last_html))

        await human_linger(page, page_no)
        last_html = await page.get_content()
        page_file = await _dump(page, f"{dump_prefix}_{page_no}")
        page_files.append(page_file)

        count = last_html.count("元/㎡")
        page_counts.append((page_no, count))

    merged_html = "\n".join(all_html_parts)
    return merged_html, page_counts, page_files, last_html


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
        deal_match = re.search(
            r'href=["\'](//[^"\']+?/loupan/\d+/chengjiao/[^"\']*)["\']',
            detail_html,
        )
        if deal_match:
            deal_url = f"https:{deal_match.group(1)}" if deal_match.group(1).startswith("//") else deal_match.group(1)
            log.info("导航到成交页: %s", deal_url)
            await detail_tab.get(deal_url)
            await detail_tab
            log.info("成交页当前 URL: %s", detail_tab.target.url)
        else:
            log.warning("详情页未找到成交页链接")
            return deal_prices, deal_record_dicts

        await _dump(detail_tab, "fang_deal")
        deal_html = await detail_tab.get_content()
        all_deals = _parse_deal_records(deal_html)
        filtered_deals = _filter_deal_records(all_deals, area_min, area_max, months=6)
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
# HTML 解析
# ============================================================

def _parse_listing_snapshots(html: str) -> list:
    """从主结果区提取在售房源快照。

    DOM:
      <dl class="clearfix ...">
        <dd><h4><a><span class="tit_shop">小区名 房源标题...</span></a></h4>
            <p class="tel_shop">3室2厅 | 88.35㎡ | ...</p></dd>
        <dd class="price_right"><span class="red"><b>530</b>万</span><span>59988元/㎡</span></dd>
      </dl>

    边界：截断到"您可能感兴趣的新房"(InterestedNewHouse)之前，排除新房推荐位。
    注意：房天下的 tit_shop 是房源标题（含小区名），不像安居客有独立小区名字段。
    """
    cut = html.find("InterestedNewHouse")
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(r'<dl class="clearfix[^"]*"[^>]*>(.*?)</dl>', main_html, re.S):
        chunk = block.group(1)

        # 小区名：tit_shop 取第一个词（房天下 tit_shop 是房源标题，含小区名）
        name_m = re.search(r'tit_shop[^>]*>(.*?)</span>', chunk, re.S)
        community_name = None
        if name_m:
            clean = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()
            community_name = clean.split()[0] if clean else None

        # 户型+面积：tel_shop 里 "3室2厅 | 88.35㎡ | ..."
        tel_m = re.search(r'tel_shop[^>]*>(.*?)</p>', chunk, re.S)
        layout = None
        area = None
        if tel_m:
            tel_text = re.sub(r'<[^>]+>', '', tel_m.group(1))
            layout_m = re.search(r'(\d+室\d+厅)', tel_text)
            if layout_m:
                layout = layout_m.group(1)
            area_m = re.search(r'([\d.]+)\s*㎡', tel_text)
            if area_m:
                area = float(area_m.group(1))

        # 总价(万)
        total_m = re.search(r'<b>([\d,]+)</b>\s*万', chunk)
        total_price = float(total_m.group(1).replace(",", "")) if total_m else None

        # 单价
        price_m = re.search(r'<span>([\d,]+)\s*元/㎡</span>', chunk)
        unit_price = float(price_m.group(1).replace(",", "")) if price_m else None

        if unit_price is None and total_price is None:
            continue

        snapshots.append(
            ListingSnapshot(
                house_id="",
                community_name=community_name,
                area=area,
                layout=layout,
                unit_price=unit_price,
                total_price=total_price,
            )
        )
    return snapshots


def _parse_deal_records(html: str) -> list:
    """从成交页表格解析成交记录。

    DOM:
      <table class="table_hx"><tbody>
        <tr><th>房源面积</th><th>成交时间</th><th>成交总价</th><th>成交均价</th><th>信息来源</th></tr>
        <tr><td><p>75.14㎡</p></td><td><p>2026-05-06</p></td><td><p>558万</p></td><td><p>74262元/㎡</p></td>...</tr>

    返回 [(面积, 日期, 总价万, 单价), ...] 原始列表，不过滤。
    """
    rows = re.findall(
        r'<td><p>([\d.]+)\s*㎡</p></td>\s*'
        r'<td><p>(\d{4}-\d{2}-\d{2})</p></td>\s*'
        r'<td><p>([\d]+)万</p></td>\s*'
        r'<td><p>([\d,]+)\s*元/㎡</p></td>',
        html,
    )
    records = []
    for area_str, date_str, total_str, price_str in rows:
        try:
            records.append((
                float(area_str),
                date_str,
                int(total_str),
                float(price_str.replace(",", "")),
            ))
        except ValueError:
            continue
    return records


def _filter_deal_records(records: list, area_min: float, area_max: float, months: int = 6) -> list:
    """过滤成交记录：严格面积区间 + 近 months 个月。

    房天下规则：严格面积区间 area_min~area_max（不套容差），日期 >= 今天往前 months 个月。
    注意：贝壳用的是 ±20% 容差（parsers.filter_deal_prices_by_area），
    房天下按业务确认用严格区间，两者口径不同，各自实现，不混用。
    """
    cutoff = datetime.now() - timedelta(days=30 * months)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    filtered = []
    for area, date_str, total, price in records:
        if not (area_min <= area <= area_max):
            continue
        if date_str < cutoff_str:
            continue
        filtered.append((area, date_str, total, price))
    return filtered


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


async def reset_to_start_page(page):
    """回到房天下二手房首页，并获取新的页面上下文。"""
    refreshed_page = await page.get(START_URL)
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
    if _is_login_html(html):
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
    area_min: float,
    area_max: float,
    request_id: Optional[str] = None,
) -> PlatformResult:
    """执行一次完整的房天下询价采集。"""
    start = time.time()
    log.info("收到请求: 小区=%s 面积=%.0f~%.0f㎡", community_name, area_min, area_max)
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
    area_min: float,
    area_max: float,
    request_id: Optional[str],
    started_at: float,
) -> PlatformResult:
    # 1. 刷新首页保活
    main_page = await reset_to_start_page(main_page)
    await _dump(main_page, "fang_refresh")

    # 2. 搜索小区
    keyword_html = await _search_community(main_page, community_name)
    await _dump(main_page, "fang_keyword_result")
    keyword_url = main_page.target.url or ""

    # 3. 判风控/登录
    if _is_captcha_url(keyword_url) or _is_captcha_html(keyword_html):
        return PlatformResult(
            name="房天下",
            status="WAIT_MANUAL_VERIFY",
            reason="搜索后命中验证码拦截",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )
    if _is_login_html(keyword_html):
        return PlatformResult(
            name="房天下",
            status="LOGIN_EXPIRED",
            reason="搜索后进入登录页",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    # 4. 填面积筛选
    log.info("填写面积筛选: %d-%d", area_min, area_max)
    area_confirmed = await _fill_area_inputs(main_page, area_min, area_max)
    await _dump(main_page, "fang_after_area")

    area_url = main_page.target.url or ""
    area_html = await main_page.get_content()
    if _is_captcha_url(area_url) or _is_captcha_html(area_html):
        return PlatformResult(
            name="房天下",
            status="WAIT_MANUAL_VERIFY",
            reason="面积筛选后命中验证码拦截",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )
    if not area_confirmed:
        return PlatformResult(
            name="房天下",
            status="ERROR",
            reason="面积筛选未能成功提交",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    # 5. 点开小区详情（Ctrl+点击后台新标签，入口只在第一页有，翻页前必须点）
    log.info("点击小区详情（分页前先点开）")
    detail_clicked, detail_tab = await _click_detail_link(browser, main_page)
    if detail_clicked and detail_tab is not None:
        log.info("详情标签已打开")
    else:
        log.warning("未能打开小区详情页")

    # 6. 并行：分页采集在售房源 + 导航成交页
    total_pages = _parse_total_pages(area_html)
    log.info("总页数: %d", total_pages)

    # 启动成交页导航任务（后台并行，解析完自动关成交 tab 切回主页）
    deal_prices_future: Optional[asyncio.Task] = None
    deal_record_dicts_future: Optional[asyncio.Task] = None
    if detail_tab is not None:
        deal_prices_future = asyncio.ensure_future(
            _navigate_and_parse_deals(detail_tab, main_page, area_min, area_max)
        )

    # 分页采集在售房源（主线程，和成交导航并行）
    merged_html, page_counts, page_files, last_page_html = await _collect_listing_pages(
        main_page, area_html, total_pages
    )
    log.info("分页采集完成: 每页 %s", page_counts)

    # 7. 解析在售房源
    snapshots = _parse_listing_snapshots(merged_html)
    quote_prices = [s.unit_price for s in snapshots if s.unit_price]
    if not quote_prices:
        return PlatformResult(
            name="房天下",
            status="NO_DATA",
            reason="面积结果页未抓到在售单价",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
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
