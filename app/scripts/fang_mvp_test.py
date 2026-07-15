# -*- coding: utf-8 -*-
"""房天下 MVP 测试脚本。

完整业务链路：打开首页 → 人工登录 → 搜索小区 → 面积筛选 →
分页翻页 → 在售解析 → 详情页 → 成交记录 → 算法决策。

用法：
  python -m app.scripts.fang_mvp_test --manual-login --debug
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import nodriver as uc

from app.core import config
from app.core.algorithm import decide
from app.utils.debug_utils import dump_html as shared_dump_html
from app.utils.debug_utils import set_debug_mode
from app.core.models import ListingSnapshot
from app.core.price_utils import format_price
from app.utils.mvp_result import print_mvp_result
from app.utils.logging_utils import setup_logging

setup_logging()
log = logging.getLogger("fang-mvp-test")

# 房天下深圳二手房首页
# 二手房是 esf 子域：https://{城市拼音缩写}.esf.fang.com/
START_URL = "https://sz.esf.fang.com/"

# 固定测试场景：与贝壳/安居客脚本一致
COMMUNITY_NAME = "绿景虹湾"
AREA_MIN = 70
AREA_MAX = 90


# ============================================================
# 通用：HTML 导出 / 拦截判定 / 人工等待
# ============================================================

async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def is_captcha_url(url: str) -> bool:
    """房天下验证码拦截页 URL 特征（具体待 dump 核对后收敛）。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def is_captcha_html(html: str) -> bool:
    markers = (
        "请输入验证码",
        "验证后继续访问",
        "请完成验证",
        "滑动验证",
    )
    return any(marker in html for marker in markers)


def is_login_html(html: str) -> bool:
    markers = (
        "请输入手机号",
        "请输入密码",
        "手机快捷登录",
        "扫码登录",
    )
    return any(marker in html for marker in markers)


async def wait_for_manual_login():
    prompt = (
        "\n请在打开的浏览器里手动完成验证码 / 登录。"
        "\n完成后回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


async def wait_for_manual_close():
    prompt = (
        "\n浏览器将保持打开，方便你现场查看。"
        "\n看完后回到终端按回车结束脚本...\n"
    )
    await asyncio.to_thread(input, prompt)


# ============================================================
# 第2步：搜索框定位 / 提交 / 真人点击
# ============================================================

async def is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def get_search_input(page):
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
            if await is_interactable(element):
                return selector, element
        if elements:
            return selector, elements[0]
    return None, None


async def get_search_submit(page):
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
            if await is_interactable(element):
                return selector, element
        if elements:
            return selector, elements[0]
    return None, None


async def human_click(page, element, label: str) -> bool:
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
    for clicker in ("mouse", "js"):
        try:
            if clicker == "mouse":
                await element.mouse_click()
            else:
                await element.click()
            await page
            await asyncio.sleep(1.0)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s click failed: %s", label, last_error)
    return False


# ============================================================
# 第3步：面积自定义输入（房天下也是填值+点确定）
# ============================================================

async def fill_area_inputs(page, area_min, area_max):
    """定位面积筛选输入框并填值，返回是否点击确定成功。

    房天下 DOM（id 重复，取第一组）：
      <li class="text_inp" name="customarea">
        <input id="cminArea" ...> - <input id="cmaxArea" ...>
        <input id="aConfirmButton" type="button" value="确定" style="display:none;">
      </li>
    """
    # 用 customarea 容器定位，避免取到重复的第二组
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
    await human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)
    log.info("[3] 填入下限: %s", area_min)

    # 填上限
    await human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)
    log.info("[3] 填入上限: %s", area_max)

    # 点"确定"提交（填值后从 display:none 变可见）
    confirms = await container.query_selector_all("#aConfirmButton")
    log.info("[3] 确定按钮数量: %d", len(confirms))
    confirm_clicked = False
    if confirms:
        confirm_clicked = await human_click(page, confirms[0], "area confirm")
    if not confirm_clicked:
        try:
            await max_el.send_keys("\r")
            await page
            confirm_clicked = True
            log.info("[3] 用回车兜底提交")
        except Exception:
            pass

    await page
    await asyncio.sleep(3)
    return confirm_clicked


# ============================================================
# 第4步：分页采集（房天下分页：div.page_box，参考贝壳分页写法）
# ============================================================

def parse_total_pages(html: str) -> int:
    """从房天下分页区解析总页数。

    DOM: <span class="last">共2页</span>
    """
    m = re.search(r'共(\d+)页', html or "")
    return int(m.group(1)) if m else 1


def parse_current_page(html: str) -> Optional[int]:
    """从房天下分页区解析当前页码。

    DOM: <span class="on">1</span>（当前页带 class=on）
    """
    m = re.search(r'<span class="on">(\d+)</span>', html or "")
    return int(m.group(1)) if m else None


async def wait_for_results_loaded(page, expected_page: Optional[int] = None, timeout: float = 15) -> str:
    """等待结果页加载完成。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last_html = ""
    while asyncio.get_event_loop().time() < deadline:
        last_html = await page.get_content()
        if expected_page is None or parse_current_page(last_html) == expected_page:
            await asyncio.sleep(1.2)
            return last_html
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.2)
    return last_html or await page.get_content()


async def click_page_number(page, page_no: int) -> str:
    """点击房天下页码按钮，返回加载完成后的 HTML。

    DOM: <div class="page_box"><div class="page_al">
           <span><a href="...">2</a></span>
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

    if not await human_click(page, target, f"page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")

    return await wait_for_results_loaded(page, expected_page=page_no)


async def human_linger_on_result_page(page, page_no: int, linger_seconds: float = config.PAGE_LINGER_SECONDS):
    """真人式滚动停留，参考贝壳 human_linger_on_result_page。

    渐进式滚动到 25%/55%/82%/100%，每档停留，总停留约 PAGE_LINGER_SECONDS。
    """
    log.info("[4] 第 %d 页真人滚动停留 %.1fs", page_no, linger_seconds)
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


async def collect_listing_pages(page, first_page_html: str, total_pages: int, dump_prefix: str = "fang_area_page"):
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
    page_files: list[Optional[Path]] = []
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            last_html = await click_page_number(page, page_no)
            all_html_parts.append(_cut_main(last_html))

        # 真人滚动停留（参考贝壳）
        await human_linger_on_result_page(page, page_no)
        last_html = await page.get_content()
        page_file = await dump_html(page, f"{dump_prefix}_{page_no}")
        page_files.append(page_file)

        # 粗略统计本页在售数量（只认元/㎡，不认元/平米）
        count = last_html.count("元/㎡")
        page_counts.append((page_no, count))
        log.info("[4] 第 %d 页在售: %d", page_no, count)

    # 合并所有页的主结果区
    merged_html = "\n".join(all_html_parts)
    return merged_html, page_counts, page_files, last_html


# ============================================================
# 第5步：点开小区详情（新标签，入口只在第一页有）
# 方案B：分页采集前先点开详情（target=_blank 新标签不影响主页面翻页），
#        采完在售再切到详情标签抓均价/成交。
# ============================================================

async def wait_for_new_tab(browser, old_tab_ids: set, expected_url, timeout=20):
    """等待新标签页打开，参考 ke_adapter._wait_for_new_tab。"""
    for _ in range(int(timeout / 0.5)):
        await asyncio.sleep(0.5)
        for tab in browser.tabs:
            if id(tab) not in old_tab_ids:
                return tab
            if expected_url and (tab.target.url or "").startswith(expected_url):
                return tab
    return None


async def click_detail_link(browser, page):
    """Ctrl+点击"小区详情"在后台打开新标签页，返回 (是否成功, 详情标签页)。

    DOM: <a class="href_xq" target="_blank" href="/loupan/xxx.htm">小区详情&gt;</a>
    入口只在第一页有，必须在翻页前点。

    关键：必须用 Ctrl+点击（modifiers=2），让新标签在后台打开，
    焦点留在当前主页面，否则详情标签会抢占焦点导致主页面翻页卡住。
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

    # 滚动到可见
    try:
        await detail_link.scroll_into_view()
    except Exception:
        pass
    await asyncio.sleep(0.3)

    # Ctrl+点击：取元素中心坐标，用 Tab.mouse_click 传 modifiers=2
    # Element.mouse_click 的 modifiers 参数实际没传给 CDP，所以用 Tab 级别
    try:
        pos = await detail_link.get_position()
        if pos and pos.center:
            cx, cy = pos.center
            await page.mouse_click(cx, cy, modifiers=2)
            await page
            await asyncio.sleep(1.5)
        else:
            return False, None
    except Exception as exc:
        log.warning("ctrl+click detail link failed: %s", exc)
        return False, None

    detail_tab = await wait_for_new_tab(browser, old_tab_ids, "/loupan/")
    return detail_tab is not None, detail_tab


# ============================================================
# 第6步：解析成交记录（严格面积区间 + 近半年）
# ============================================================

def parse_deal_records(html: str) -> list:
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


def filter_deal_records(records: list, area_min: float, area_max: float, months: int = 6) -> list:
    """过滤成交记录：严格面积区间 + 近 months 个月。

    房天下规则：严格 70-90㎡（不套容差），日期 >= 今天往前 months 个月。
    返回过滤后的 [(面积, 日期, 总价万, 单价), ...]。
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


def parse_listing_snapshots(html: str) -> list:
    """从主结果区提取在售房源快照。

    DOM:
      <dl class="clearfix ...">
        <dt>...图片...</dt>
        <dd>
          <h4><a><span class="tit_shop">绿景虹湾 房源标签...</span></a></h4>
          <p class="tel_shop">3室2厅 | 88.35㎡ | ...</p>
        </dd>
        <dd class="price_right"><span class="red"><b>530</b>万</span><span>59988元/㎡</span></dd>
      </dl>

    边界：截断到"您可能感兴趣的新房"(InterestedNewHouse)之前，排除新房推荐位。
    """
    cut = html.find("InterestedNewHouse")
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(r'<dl class="clearfix[^"]*"[^>]*>(.*?)</dl>', main_html, re.S):
        chunk = block.group(1)

        # 小区名：tit_shop 取第一个词
        name_m = re.search(r'tit_shop[^>]*>(.*?)</span>', chunk, re.S)
        community_name = None
        if name_m:
            community_name = re.sub(r'<[^>]+>', '', name_m.group(1)).strip().split()[0] if re.sub(r'<[^>]+>', '', name_m.group(1)).strip() else None

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


def print_listing_snapshots(snapshots: list):
    """对齐 batch_mvp_test.print_platform_details 的打印格式。"""
    if not snapshots:
        print("房天下: 未抓到房源摘要")
        return
    for item in snapshots:
        print(
            "房天下: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )


# ============================================================
# 结果汇报
# ============================================================

def print_summary(
    *,
    open_file: Optional[Path],
    before_file: Optional[Path],
    after_file: Optional[Path],
    area_file: Optional[Path],
    error_file: Optional[Path],
    open_url: str,
    open_blocked: bool,
    open_block_reason: Optional[str],
    search_input_selector: Optional[str],
    submit_selector: Optional[str],
    result_url: str,
    result_title: Optional[str],
    result_blocked: bool,
    result_block_reason: Optional[str],
    area_confirmed: bool,
    area_url: str,
    area_prices_count: int,
    detail_clicked: bool,
    detail_file: Optional[Path],
    detail_url: str,
    detail_title: Optional[str],
    deal_file: Optional[Path],
    deal_url: str,
    deal_title: Optional[str],
    all_deals_count: int,
    filtered_deals_count: int,
    deal_avg: Optional[float],
    body_len: Optional[int],
    prices_count: int,
    conclusion: str,
    listing_snapshots: list,
    filtered_deals: list,
    quote_avg: Optional[float],
):
    print_mvp_result(
        platform="房天下",
        community_name=COMMUNITY_NAME,
        area_min=AREA_MIN,
        area_max=AREA_MAX,
        trace={
            "home_blocked": open_blocked,
            "search_url": result_url,
            "area_ok": area_confirmed,
            "area_url": area_url,
            "area_pages": 0,
            "detail_ok": detail_clicked,
            "detail_url": detail_url,
        },
        listings={
            "count": len(listing_snapshots),
            "avg": quote_avg,
            "snapshots": listing_snapshots,
        },
        deals={
            "count": len(filtered_deals),
            "avg": deal_avg,
            "records": [],
        },
        result={
            "quote_avg": quote_avg or 0,
            "deal_avg": deal_avg,
            "final_price": final_price or 0,
            "branch": branch,
        },
        elapsed=0,
    )


async def main(manual_login: bool = False, debug: bool = False):
    if debug:
        set_debug_mode(True)

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    page = None
    open_file = None
    before_file = None
    after_file = None
    area_file = None
    open_url = ""
    open_blocked = False
    open_block_reason: Optional[str] = None
    search_input_selector = None
    submit_selector = None
    result_url = ""
    result_title: Optional[str] = None
    result_blocked = False
    result_block_reason: Optional[str] = None
    body_len: Optional[int] = None
    prices_count = 0
    area_confirmed = False
    area_url = ""
    area_prices_count = 0
    page_counts: list = []
    detail_clicked = False
    detail_tab = None
    detail_file = None
    detail_url = ""
    detail_title: Optional[str] = None
    deal_file = None
    deal_url = ""
    deal_title: Optional[str] = None
    all_deals: list = []
    filtered_deals: list = []
    deal_prices: list = []
    deal_avg = None
    quote_prices: list = []
    listing_snapshots: list = []
    quote_avg = None
    final_price = None

    try:
        # ---- 第1步：打开首页 ----
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        open_file = await dump_html(page, "fang_opened")

        open_url = page.target.url or ""
        open_html = await page.get_content()
        if is_captcha_url(open_url) or is_captcha_html(open_html):
            open_blocked = True
            open_block_reason = "命中验证码拦截"
        elif is_login_html(open_html):
            open_blocked = True
            open_block_reason = "命中登录页"
        log.info("[1] 首次打开 URL: %s, 是否被拦: %s", open_url, open_blocked)

        # ---- 人工处理后重新打开 ----
        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(START_URL)
            await page
            await asyncio.sleep(3)

        before_file = await dump_html(page, "fang_search_before")

        # ---- 第2步：搜索绿景虹湾 ----
        search_input_selector, inp = await get_search_input(page)
        if inp is None:
            raise RuntimeError("未找到房天下搜索框")
        log.info("[2] 搜索框命中: %s", search_input_selector)

        clicked = await human_click(page, inp, "search input")
        if not clicked:
            raise RuntimeError("搜索框未能成功点击")

        try:
            await inp.clear_input()
        except Exception:
            pass
        await asyncio.sleep(0.5)
        await inp.send_keys(COMMUNITY_NAME)
        await page
        await asyncio.sleep(1.0)

        # 优先点搜索按钮；失败回车兜底
        submitted = False
        submit_selector, submit_btn = await get_search_submit(page)
        if submit_btn and await human_click(page, submit_btn, "search submit"):
            await page
            await asyncio.sleep(3)
            submitted = True

        if not submitted:
            try:
                await inp.send_keys("\r")
                await page
                await asyncio.sleep(3)
                submitted = True
                submit_selector = "Enter key"
            except Exception:
                pass

        if not submitted:
            raise RuntimeError("未能提交搜索")

        after_file = await dump_html(page, "fang_search_after")

        # 探测结果页
        result_url = page.target.url or ""
        result_html = await page.get_content()
        try:
            result_title = await page.evaluate("document.title", return_by_value=True)
            body_len = await page.evaluate("document.body.innerText.length", return_by_value=True)
        except Exception as exc:
            log.warning("读取结果页信息失败: %s", exc)

        if is_captcha_url(result_url) or is_captcha_html(result_html):
            result_blocked = True
            result_block_reason = "命中验证码拦截"
        elif is_login_html(result_html):
            result_blocked = True
            result_block_reason = "命中登录页"

        if not result_blocked:
            prices_count = result_html.count("元/㎡") + result_html.count("元/平米")

        # ---- 第3步：面积自定义筛选 70-90 ----
        if not result_blocked:
            log.info("[3] 填写面积筛选: %d-%d", AREA_MIN, AREA_MAX)
            area_confirmed = await fill_area_inputs(page, AREA_MIN, AREA_MAX)
            area_file = await dump_html(page, "fang_after_area")

            area_url = page.target.url or ""
            area_html = await page.get_content()
            if is_captcha_url(area_url) or is_captcha_html(area_html):
                result_blocked = True
                result_block_reason = "面积筛选后命中验证码拦截"
            area_prices_count = area_html.count("元/㎡")
            log.info("[3] 面积筛选后 URL: %s, 在售数量: %d", area_url, area_prices_count)

            # ---- 第5步：先点开小区详情（新标签，入口只在第一页有）----
            # 方案B：分页前点开详情(target=_blank 不影响主页面)，采完在售再切回去抓
            log.info("[5] 点击小区详情（分页前先点开）")
            detail_clicked, detail_tab = await click_detail_link(browser, page)
            if detail_clicked and detail_tab is not None:
                log.info("[5] 详情标签已打开")
            else:
                log.warning("[5] 未能打开小区详情页")

            # ---- 第4步：分页采集（参考贝壳分页写法）----
            total_pages = parse_total_pages(area_html)
            log.info("[4] 总页数: %d", total_pages)
            merged_html, page_counts, page_files, last_page_html = await collect_listing_pages(
                page, area_html, total_pages
            )
            # 合并后重新统计在售总数（只认"元/㎡"，不认"元/平米"，后者会纳入新房推荐位）
            area_prices_count = merged_html.count("元/㎡")
            log.info("[4] 分页采集完成: 每页 %s, 合计在售 %d", page_counts, area_prices_count)

            # 解析在售房源快照，算在售均价
            listing_snapshots = parse_listing_snapshots(merged_html)
            quote_prices = [s.unit_price for s in listing_snapshots if s.unit_price]
            quote_avg = sum(quote_prices) / len(quote_prices) if quote_prices else None
            log.info("[4] 在售房源解析: %d 条, 在售均价 %s", len(quote_prices),
                     f"{quote_avg:.2f}" if quote_avg else "None")

            # ---- 第5步续：切到详情标签（激活焦点），dump 详情页 HTML ----
            if detail_tab is not None:
                try:
                    await detail_tab.activate()
                    await detail_tab
                except Exception as exc:
                    log.warning("[5] 激活详情标签失败: %s", exc)
                await asyncio.sleep(3)
                detail_file = await dump_html(detail_tab, "fang_detail")
                detail_url = detail_tab.target.url or ""
                try:
                    detail_title = await detail_tab.evaluate("document.title", return_by_value=True)
                except Exception:
                    detail_title = None
                log.info("[5] 详情页 URL: %s, 标题: %s", detail_url, detail_title)

                # ---- 第5步续：点"小区成交"tab（同页跳转到 /loupan/{id}/chengjiao/）----
                log.info("[5] 点击小区成交 tab")
                deal_clicked = False
                # 优先按文本定位（最稳），排除顶部导航的"查成交"全市链接
                try:
                    deal_link = await detail_tab.find("小区成交", timeout=4)
                except Exception:
                    deal_link = None
                if deal_link and await human_click(detail_tab, deal_link, "deal tab"):
                    deal_clicked = True
                # 兜底：精确选择器，必须同时含 /loupan/ 和 /chengjiao/
                if not deal_clicked:
                    for selector in ("a[href*='/loupan/'][href*='/chengjiao/']",):
                        try:
                            deal_link = await detail_tab.select(selector, timeout=4)
                        except Exception:
                            deal_link = None
                        if deal_link and await human_click(detail_tab, deal_link, "deal tab"):
                            deal_clicked = True
                            break

                if deal_clicked:
                    await detail_tab
                    await asyncio.sleep(3)
                    deal_file = await dump_html(detail_tab, "fang_deal")
                    deal_url = detail_tab.target.url or ""
                    try:
                        deal_title = await detail_tab.evaluate("document.title", return_by_value=True)
                    except Exception:
                        deal_title = None
                    log.info("[5] 成交页 URL: %s, 标题: %s", deal_url, deal_title)

                    # ---- 第6步：解析成交记录（严格面积 + 近半年）----
                    deal_html = await detail_tab.get_content()
                    all_deals = parse_deal_records(deal_html)
                    filtered_deals = filter_deal_records(all_deals, AREA_MIN, AREA_MAX, months=6)
                    deal_prices = [d[3] for d in filtered_deals]
                    deal_avg = sum(deal_prices) / len(deal_prices) if deal_prices else None
                    log.info(
                        "[6] 成交记录: 总 %d 条, 70-90㎡且近半年 %d 条, 成交均价 %s",
                        len(all_deals), len(filtered_deals),
                        f"{deal_avg:.2f}" if deal_avg else "None",
                    )
                    print()
                    print("-" * 60)
                    print(f"成交记录（70-90㎡，近半年）共 {len(filtered_deals)} 条：")
                    for area, date_str, total, price in filtered_deals:
                        print(f"  {area}㎡ {date_str} {total}万 {price}元/㎡")
                    if deal_avg:
                        print(f"成交均价: {deal_avg:.2f} 元/㎡")
                    print("-" * 60)
                else:
                    log.warning("[5] 未能点击小区成交 tab")

            # ---- 算最终价：在售均价 vs 成交均价 ----
            if quote_avg is not None or deal_avg is not None:
                decision = decide(
                    quote_avg=quote_avg,
                    deal_avg=deal_avg,
                    diff_threshold=config.DEAL_DIFF_THRESHOLD,
                    no_deal_discount=config.get_no_deal_discount(),
                )
                final_price = decision.final_price

                # 输出对齐 batch_mvp_test 格式
                print()
                print("=" * 60)
                print("房天下 平台结果")
                print_listing_snapshots(listing_snapshots)
                print()
                print(f"在售均价(单位:元/平): {format_price(quote_avg)}")
                print(f"成交均价(单位:元/平): {format_price(deal_avg)}")
                print(f"最终取值(单位:元/平): {format_price(final_price)}")
                print()
                print("模拟返回 body")
                print(
                    "{"
                    f'"quoteAvg": {format_price(quote_avg)}, '
                    f'"dealAvg": {format_price(deal_avg)}, '
                    f'"finalPrice": {format_price(final_price)}'
                    "}"
                )
                print("=" * 60)

        # 判定结论
        if result_blocked:
            conclusion = f"流程被拦：{result_block_reason}，需人工处理或重试。"
        elif not area_confirmed:
            conclusion = "面积筛选未能成功提交，需查看 HTML 确认输入框与确定按钮。"
        elif area_prices_count > 0:
            conclusion = (
                f"采集成功：{AREA_MIN}-{AREA_MAX}㎡ 分页 {len(page_counts)} 页，合计在售 {area_prices_count} 条。"
            )
        elif prices_count > 0:
            conclusion = "搜索成功但面积筛选后未识别到在售，需查看筛选后 HTML。"
        else:
            conclusion = "流程已执行，但未识别到在售房源，需查看 HTML 确认 DOM 结构。"

        print_summary(
            open_file=open_file,
            before_file=before_file,
            after_file=after_file,
            area_file=area_file,
            error_file=None,
            open_url=open_url,
            open_blocked=open_blocked,
            open_block_reason=open_block_reason,
            search_input_selector=search_input_selector,
            submit_selector=submit_selector,
            result_url=result_url,
            result_title=result_title,
            result_blocked=result_blocked,
            result_block_reason=result_block_reason,
            area_confirmed=area_confirmed,
            area_url=area_url,
            area_prices_count=area_prices_count,
            detail_clicked=detail_clicked,
            detail_file=detail_file,
            detail_url=detail_url,
            detail_title=detail_title,
            deal_file=deal_file,
            deal_url=deal_url,
            deal_title=deal_title,
            all_deals_count=len(all_deals),
            filtered_deals_count=len(filtered_deals),
            deal_avg=deal_avg,
            body_len=body_len,
            prices_count=prices_count,
            conclusion=conclusion,
        )

        await wait_for_manual_close()
    except Exception:
        error_file = None
        if page is not None:
            error_file = await dump_html(page, "fang_error")
        log.exception("测试异常中断")
        raise
    finally:
        browser.stop()


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="首次打开若被拦截，先人工过验证码 / 登录，回车后重新打开再探测。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML 到 debug 目录。",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(main(manual_login=args.manual_login, debug=args.debug))


if __name__ == "__main__":
    cli()
