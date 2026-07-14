# -*- coding: utf-8 -*-
"""链家 MVP 测试脚本。

逐步迭代：在同一脚本里一步步验证链家链路，不另建脚本。
当前覆盖：第1步 首页打开 + 探测拦截。

链家二手房：https://{城市拼音缩写}.lianjia.com/ershoufang/
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import nodriver as uc

from app.core import config
from app.core.algorithm import decide
from app.utils.debug_utils import dump_html as shared_dump_html
from app.utils.debug_utils import set_debug_mode
from app.core.models import ListingSnapshot
from app.core.price_utils import format_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("lj-mvp-test")

# 链家深圳二手房首页
START_URL = "https://sz.lianjia.com/ershoufang/"

# 固定测试场景：与贝壳/安居客/房天下脚本一致
COMMUNITY_NAME = "绿景虹湾"
AREA_MIN = 70
AREA_MAX = 90


# ============================================================
# 通用：HTML 导出 / 拦截判定 / 人工等待
# ============================================================

async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def is_captcha_url(url: str) -> bool:
    """验证码拦截页 URL 特征。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def is_captcha_html(html: str) -> bool:
    markers = (
        "请输入验证码",
        "验证后继续访问",
        "请完成验证",
        "滑动验证",
        "人机验证",          # 链家/贝壳安全中心
        "贝壳信息安全中心",
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
# 第2步：页面交互辅助
# ============================================================

async def is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


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
# 第2步：搜索小区
#    链家搜索 URL 格式：/ershoufang/rs{小区名}/
#    页面上的 search_input + 回车不可靠（AJAX 搜索 URL 不变），改用 URL 直接导航。
# ============================================================

async def _search_community(page, community_name: str) -> str:
    """搜索小区：在已登录的首页填搜索框 + 真人点击搜索按钮提交。

    DOM:
      <form id="searchForm" action="/ershoufang/rs">
        <input type="text" id="searchInput" ...>
        <button type="submit" class="searchButton" ...>&nbsp;<i></i>&nbsp;</button>
      </form>
    """
    try:
        inp = await page.select("#searchInput", timeout=3)
    except Exception:
        inp = None
    if inp is None:
        raise RuntimeError("未找到搜索框 #searchInput")

    if not await human_click(page, inp, "search input"):
        raise RuntimeError("搜索框未能成功点击")

    try:
        await inp.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.5)
    await inp.send_keys(community_name)
    await page
    await asyncio.sleep(1.0)

    # 真人点击搜索按钮 button.searchButton
    try:
        submit_btn = await page.select("button.searchButton", timeout=3)
    except Exception:
        submit_btn = None
    if submit_btn and await human_click(page, submit_btn, "search button"):
        await page
        await asyncio.sleep(3)
    else:
        raise RuntimeError("未能点击搜索按钮 button.searchButton")

    return await page.get_content()


# ============================================================
# 第3步：面积筛选（链家需先"更多选项"→"更多及自定义"→点面积档位）
# ============================================================

async def apply_area_filter(page, area_min, area_max):
    """链家面积筛选：更多选项 → 面积区 → 更多及自定义 → 填 min-max input → 确定。

    链家 DOM：
      1. div.more.btn-more = "更多选项"（首页需点击展开筛选区，搜索结果页可能已展开）
      2. dl.hide.hasmore 内 dt[title*="面积"] = 面积筛选区
      3. span.btn-showmore = "+ 更多及自定义"（点击后展开 customFilter）
      4. span.customFilter[data-role="area"]
         input[role="minValue"] / input[role="maxValue"] / button.btn-range[data-url*="ba{min}ea{max}"]
    """
    # 1. 检查是否有"更多选项"，有则点击展开
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
            log.info("[3] 点击'更多选项'展开筛选区")
            await human_click(page, more_btn, "更多选项")
            await page
            await asyncio.sleep(1.5)
        else:
            log.info("[3] 筛选区已展开(按钮=%s)，跳过", btn_text)

    # 2. 找面积区：dl 内 dt[title*="面积"]
    try:
        containers = await page.select_all("dl.hide.hasmore", timeout=3)
    except Exception:
        containers = []

    area_container = None
    for c in containers:
        try:
            tit = await c.apply(
                "(el) => { const t = el.querySelector('dt'); return t ? t.title || t.textContent.trim() : ''; }"
            )
        except Exception:
            tit = ""
        if "面积" in tit:
            area_container = c
            break

    if area_container is None:
        raise RuntimeError("未找到面积筛选区（含 dt[title*=面积] 的 dl）")

    # 3. 点击面积区的"更多及自定义"展开 customFilter
    try:
        btns = await area_container.query_selector_all("span.btn-showmore")
    except Exception:
        btns = []
    if btns:
        log.info("[3] 点击'更多及自定义'展开面积自定义输入")
        await human_click(page, btns[0], "更多及自定义")
        await page
        await asyncio.sleep(1.5)

    # 4. 定位 customFilter[data-role="area"] 下的 min/max input
    try:
        custom = await area_container.query_selector_all("span.customFilter[data-role='area']")
    except Exception:
        custom = []
    if not custom:
        # 兜底：在整个面积区找
        try:
            custom = await area_container.query_selector_all("span.customFilter")
        except Exception:
            custom = []
    if not custom:
        raise RuntimeError("面积区未找到 customFilter")
    custom = custom[0]

    min_inputs = await custom.query_selector_all("input[role='minValue']")
    max_inputs = await custom.query_selector_all("input[role='maxValue']")
    if not min_inputs or not max_inputs:
        raise RuntimeError("面积区未找到 minValue/maxValue input")
    min_el, max_el = min_inputs[0], max_inputs[0]

    # 5. 填下限
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

    # 6. 填上限
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

    # 7. 点"确定"提交（填值后从 hide 变可见）
    confirm_btns = await custom.query_selector_all("button.btn-range")
    log.info("[3] 确定按钮数量: %d", len(confirm_btns))
    confirm_clicked = False
    if confirm_btns:
        confirm_clicked = await human_click(page, confirm_btns[0], "area confirm")
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
    return confirm_clicked, "custom"


# ============================================================
# 第4步：解析在售房源（链家和贝壳同代码库，DOM 一致）
# ============================================================

def parse_listing_snapshots(html: str) -> list:
    """从结果页主结果列表提取在售房源快照。

    DOM（和贝壳一致）：
      <ul class="sellListContent">
        <li class="clear">
          <div class="info">
            <div class="positionInfo">...<a>绿景虹湾</a>...</div>
            <div class="houseInfo">3室2厅 | 87.57平米 | 东 | ...</div>
          </div>
          <div class="priceInfo">
            <div class="totalPrice"><span>760</span>万</div>
            <div class="unitPrice" data-price="86788"><span>86,788元/平</span></div>
          </div>
        </li>
    """
    # 截断到"猜你喜欢"之前，排除推荐位（和安居客的 list-guess-title、房天下的 InterestedNewHouse 同类）
    cut = html.find("猜你喜欢")
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(r'<li class="clear[^"]*"[^>]*>(.*?)(?=<li class="clear|$)', main_html, re.S):
        chunk = block.group(1)
        # 跳过广告/猜你喜欢（goodhouse）
        if "goodhouse" in chunk:
            continue

        # 小区名
        name_m = re.search(r'<div class="positionInfo">.*?<a[^>]*>([^<]+)</a>', chunk, re.S)
        community_name = name_m.group(1).strip() if name_m else None

        # 户型+面积
        info_m = re.search(r'<div class="houseInfo">.*?>(.*?)</div>', chunk, re.S)
        layout = None
        area = None
        if info_m:
            info_text = re.sub(r'<[^>]+>', '', info_m.group(1))
            layout_m = re.search(r'(\d+室\d+厅)', info_text)
            if layout_m:
                layout = layout_m.group(1)
            area_m = re.search(r'([\d.]+)\s*平米', info_text)
            if area_m:
                area = float(area_m.group(1))

        # 总价(万)
        total_m = re.search(r'class="totalPrice[^"]*"[^>]*>.*?<span[^>]*>([\d.]+)</span>', chunk, re.S)
        total_price = float(total_m.group(1)) if total_m else None

        # 单价（优先用 data-price 属性）
        unit_m = re.search(r'class="unitPrice"[^>]*data-price="([\d]+)"', chunk)
        if not unit_m:
            unit_m = re.search(r'class="unitPrice"[^>]*>.*?<span>([\d,]+)\s*元', chunk, re.S)
        unit_price = float(unit_m.group(1).replace(",", "")) if unit_m else None

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
        print("链家: 未抓到房源摘要")
        return
    for item in snapshots:
        print(
            "链家: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )


# ============================================================
# 第4步续：在售分页采集（参考贝壳 collect_listing_pages）
# ============================================================

def parse_listing_total_pages(html: str) -> int:
    """从在售结果页分页区解析总页数。

    DOM（和贝壳一致）：
      <div class="page-box house-lst-page-box"
           page-data="{&quot;totalPage&quot;:3,&quot;curPage&quot;:1}">
    """
    m = re.search(r'totalPage&quot;:(\d+)', html or "")
    return int(m.group(1)) if m else 1


async def click_listing_page_number(page, page_no: int) -> str:
    """点击在售页码，返回加载完成后的 HTML。

    DOM（和贝壳一致）：house-lst-page-box 下 a[data-page='{n}']
    """
    selector = f".house-lst-page-box a[data-page='{page_no}']"
    try:
        element = await page.select(selector, timeout=3)
    except Exception:
        element = None
    if not element:
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")
    if not await human_click(page, element, f"listing page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")
    await page
    await asyncio.sleep(3)
    return await page.get_content()


async def human_linger_on_result_page(page, page_no: int, linger_seconds: float = config.PAGE_LINGER_SECONDS):
    """真人式滚动停留，参考贝壳 human_linger_on_result_page。"""
    import time
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


async def collect_listing_pages(page, first_page_html: str, total_pages: int):
    """逐页采集在售 HTML，合并后返回。参考贝壳 collect_listing_pages。

    每页截断到"猜你喜欢"之前，再拼接。
    翻页前真人滚动停留，翻页后检测风控，被拦则暂停等人工处理后继续。
    """
    def _cut_main(html_text: str) -> str:
        cut = html_text.find("猜你喜欢")
        return html_text[:cut] if cut > 0 else html_text

    all_html_parts: list[str] = [_cut_main(first_page_html)]
    page_counts: list[tuple[int, int]] = []
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            # 翻页前真人滚动停留（防风控）
            await human_linger_on_result_page(page, page_no)
            last_html = await click_listing_page_number(page, page_no)

            # 翻页后检测风控
            current_url = page.target.url or ""
            if is_captcha_url(current_url) or is_captcha_html(last_html) or is_login_html(last_html):
                log.warning("[4] 第 %d 页翻页后被风控拦截，等待人工处理", page_no)
                await wait_for_manual_login()
                # 人工处理后重新翻到当前页
                last_html = await click_listing_page_number(page, page_no)

            all_html_parts.append(_cut_main(last_html))
        else:
            # 第一页也停留一下
            await human_linger_on_result_page(page, page_no)

        last_html = await page.get_content()
        if page_no > 1:
            all_html_parts[-1] = _cut_main(last_html)

        count = len(parse_listing_snapshots(_cut_main(last_html)))
        page_counts.append((page_no, count))
        log.info("[4] 第 %d 页在售: %d 条", page_no, count)

    merged_html = "\n".join(all_html_parts)
    return merged_html, page_counts


# ============================================================
# 第5步：点开小区详情（新标签，参考贝壳）
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
    """点击"查看小区详情"打开新标签页，返回 (是否成功, 详情标签页)。

    DOM（和贝壳一致）: <a class="agentCardResblockLink" target="_blank" href=".../xiaoqu/xxx/">查看小区详情</a>
    """
    old_tab_ids = {id(tab) for tab in browser.tabs}

    detail_link = None
    for selector in ("a.agentCardResblockLink", "a[href*='/xiaoqu/']"):
        try:
            detail_link = await page.select(selector, timeout=4)
        except Exception:
            detail_link = None
        if detail_link:
            break

    if not detail_link:
        try:
            detail_link = await page.find("查看小区详情", timeout=3)
        except Exception:
            detail_link = None

    if not detail_link:
        return False, None

    if not await human_click(page, detail_link, "detail link"):
        return False, None

    detail_tab = await wait_for_new_tab(browser, old_tab_ids, "/xiaoqu/")
    return True, detail_tab


# ============================================================
# 第6步：成交记录解析 + 分页 + 过滤（严格面积 + 近半年）
# ============================================================

def parse_deal_records(html: str) -> list:
    """从成交页 ul.listContent 解析成交记录。

    DOM（和贝壳成交页一致）：
      <ul class="listContent"><li>
        <div class="info">
          <div class="title"><a>绿景虹湾 3室1厅 75.14平米</a></div>
          <div class="address">
            <div class="dealDate">2026.05.06</div>
            <div class="totalPrice"><span class="number">558</span>万</div>
          </div>
          <div class="flood">
            <div class="unitPrice"><span class="number">74262</span>元/平</div>
          </div>
        </div>
      </li>

    返回 [(面积, 日期, 总价万, 单价), ...]，面积从 title 提取。
    日期格式统一转为 YYYY-MM-DD（原始是 2026.05.06）。
    """
    records = []
    for m in re.finditer(r'<li[^>]*>(.*?)(?=<li[^>]*>|</ul>)', html, re.S):
        chunk = m.group(1)
        if "dealDate" not in chunk:
            continue

        # 面积：从 title 文本提取 "XX.XX平米"
        title_m = re.search(r'<div class="title">.*?>(.*?)</a>', chunk, re.S)
        area = None
        if title_m:
            area_m = re.search(r'([\d.]+)\s*平米', title_m.group(1))
            if area_m:
                area = float(area_m.group(1))

        # 日期：dealDate（2026.05.06 → 2026-05-06）
        date_m = re.search(r'<div class="dealDate">([\d.]+)</div>', chunk)
        date_str = None
        if date_m:
            date_str = date_m.group(1).replace(".", "-")

        # 总价(万)
        total_m = re.search(r'class="totalPrice">.*?class="number">([\d.]+)', chunk, re.S)
        total_price = float(total_m.group(1)) if total_m else None

        # 单价
        unit_m = re.search(r'class="unitPrice">.*?class="number">([\d,]+)', chunk, re.S)
        unit_price = float(unit_m.group(1).replace(",", "")) if unit_m else None

        if area is None and unit_price is None:
            continue

        records.append((area, date_str, total_price, unit_price))
    return records


def parse_deal_total_pages(html: str) -> int:
    """从成交页分页区解析总页数。

    DOM（和贝壳一致）：page-data="{&quot;totalPage&quot;:4,&quot;curPage&quot;:1}"
    """
    m = re.search(r'totalPage&quot;:(\d+)', html or "")
    return int(m.group(1)) if m else 1


async def click_deal_page_number(page, page_no: int) -> str:
    """点击成交页页码，返回加载完成后的 HTML。

    DOM（和贝壳一致）：a[data-page='{n}']
    """
    selector = f"a[data-page='{page_no}']"
    try:
        element = await page.select(selector, timeout=3)
    except Exception:
        element = None
    if not element:
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")
    if not await human_click(page, element, f"deal page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")
    await page
    await asyncio.sleep(3)
    return await page.get_content()


def filter_deal_records(records: list, area_min: float, area_max: float, months: int = 6) -> list:
    """过滤成交记录：严格面积区间 + 近 months 个月。

    链家规则：严格面积区间（不套容差），日期 >= 今天往前 months 个月。
    和房天下规则一致。
    """
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=30 * months)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    filtered = []
    for area, date_str, total, price in records:
        if area is not None and not (area_min <= area <= area_max):
            continue
        if date_str and date_str < cutoff_str:
            continue
        filtered.append((area, date_str, total, price))
    return filtered


# ============================================================
# 第1步：打开首页 + 探测拦截
# ============================================================

def print_summary(
    *,
    open_file: Optional[Path],
    search_after_file: Optional[Path],
    area_file: Optional[Path],
    error_file: Optional[Path],
    open_url: str,
    open_title: Optional[str],
    open_blocked: bool,
    open_block_reason: Optional[str],
    search_blocked: bool,
    search_block_reason: Optional[str],
    area_confirmed: bool,
    segment_code: str,
    area_url: str,
    area_prices_count: int,
    quote_avg: Optional[float],
    detail_clicked: bool,
    detail_file: Optional[Path],
    detail_url: str,
    detail_title: Optional[str],
    community_avg_price: Optional[float],
    deal_clicked: bool,
    deal_file: Optional[Path],
    deal_url: str,
    all_deals_count: int,
    filtered_deals_count: int,
    deal_avg: Optional[float],
    body_len: Optional[int],
    conclusion: str,
):
    print()
    print("=" * 60)
    print("链家测试完成")
    print(f"打开首页 HTML: {open_file}")
    print(f"搜索后 HTML: {search_after_file}")
    print(f"面积筛选后 HTML: {area_file}")
    print(f"异常现场 HTML: {error_file}")
    print(f"首次打开 URL: {open_url}")
    print(f"首次打开标题: {open_title}")
    print(f"首次是否被拦: {open_blocked}")
    print(f"首次拦截原因: {open_block_reason}")
    print(f"搜索后是否被拦: {search_blocked}")
    print(f"搜索后拦截原因: {search_block_reason}")
    print(f"面积筛选点击: {area_confirmed}")
    print(f"面积档位: {segment_code}")
    print(f"面积筛选后 URL: {area_url}")
    print(f"面积筛选后在售数量: {area_prices_count}")
    print(f"在售均价(quoteAvg): {quote_avg}")
    print(f"小区详情点击: {detail_clicked}")
    print(f"详情页 HTML: {detail_file}")
    print(f"详情页 URL: {detail_url}")
    print(f"详情页标题: {detail_title}")
    print(f"小区均价(community_avg_price): {community_avg_price}")
    print(f"成交记录点击: {deal_clicked}")
    print(f"成交页 HTML: {deal_file}")
    print(f"成交页 URL: {deal_url}")
    print(f"成交记录总数: {all_deals_count}")
    print(f"目标面积近半年成交数: {filtered_deals_count}")
    print(f"成交均价(dealAvg): {deal_avg}")
    print(f"正文长度: {body_len}")
    print(f"结论: {conclusion}")
    print("=" * 60)
    print()


async def main(manual_login: bool = False, debug: bool = False, search_only: bool = False):
    if debug:
        set_debug_mode(True)

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    page = None
    open_file = None
    open_url = ""
    open_title: Optional[str] = None
    open_blocked = False
    open_block_reason: Optional[str] = None
    body_len: Optional[int] = None
    search_after_file = None
    search_blocked = False
    search_block_reason: Optional[str] = None
    area_file = None
    area_confirmed = False
    segment_code = ""
    area_url = ""
    area_prices_count = 0
    listing_snapshots: list = []
    quote_prices: list = []
    quote_avg = None
    detail_clicked = False
    detail_tab = None
    detail_file = None
    detail_url = ""
    detail_title: Optional[str] = None
    community_avg_price = None
    deal_clicked = False
    deal_tab = None
    deal_file = None
    deal_url = ""
    all_deals: list = []
    filtered_deals: list = []
    deal_prices: list = []
    deal_avg = None
    final_price = None

    try:
        # ---- 第1步：打开首页 ----
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        open_file = await dump_html(page, "lj_opened")

        open_url = page.target.url or ""
        open_html = await page.get_content()
        if is_captcha_url(open_url) or is_captcha_html(open_html):
            open_blocked = True
            open_block_reason = "命中验证码拦截"
        elif is_login_html(open_html):
            open_blocked = True
            open_block_reason = "命中登录页"
        log.info("[1] 首次打开 URL: %s, 是否被拦: %s", open_url, open_blocked)

        try:
            open_title = await page.evaluate("document.title", return_by_value=True)
            body_len = await page.evaluate("document.body.innerText.length", return_by_value=True)
        except Exception as exc:
            log.warning("读取页面信息失败: %s", exc)

        # ---- 人工处理后重新打开 ----
        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(START_URL)
            await page
            await asyncio.sleep(3)
            open_file = await dump_html(page, "lj_reopened")

        # ---- 第2步：搜索小区 ----
        if not open_blocked or manual_login:
            log.info("[2] 搜索小区: %s", COMMUNITY_NAME)
            try:
                result_html = await _search_community(page, COMMUNITY_NAME)
                search_after_file = await dump_html(page, "lj_search_after")
                result_url = page.target.url or ""

                # 搜索成功判定：页面含 sellListContent 和小区名
                search_success = (
                    "sellListContent" in result_html
                    and COMMUNITY_NAME in result_html
                )
                if not search_success:
                    search_blocked = True
                    search_block_reason = "搜索未成功（页面无数据）"

                log.info("[2] 搜索后 URL: %s, 搜索成功: %s", result_url, search_success)
            except Exception as exc:
                log.warning("[2] 搜索异常: %s", exc)
                search_blocked = True
                search_block_reason = f"搜索异常: {exc}"

        # ---- search-only 模式：只测搜索+在售分页，不做筛选和详情 ----
        if search_only and not search_blocked:
            log.info("[search-only] 测试在售分页采集")
            search_html = await page.get_content()
            total_pages = parse_listing_total_pages(search_html)
            log.info("[search-only] 在售总页数: %d", total_pages)
            merged_html, page_counts = await collect_listing_pages(page, search_html, total_pages)
            listing_snapshots = parse_listing_snapshots(merged_html)
            quote_prices = [s.unit_price for s in listing_snapshots if s.unit_price]
            quote_avg = sum(quote_prices) / len(quote_prices) if quote_prices else None
            area_prices_count = len(quote_prices)
            area_url = page.target.url or ""
            log.info(
                "[search-only] 分页 %d 页, 每页 %s, 合计在售 %d 条, 均价 %s",
                len(page_counts), page_counts, area_prices_count,
                f"{quote_avg:.2f}" if quote_avg else "None",
            )
            print()
            print("=" * 60)
            print("链家 平台结果（search-only）")
            print_listing_snapshots(listing_snapshots)
            print()
            print(f"在售均价(单位:元/平): {format_price(quote_avg)}")
            print("=" * 60)

            print_summary(
                open_file=open_file,
                search_after_file=search_after_file,
                area_file=None,
                error_file=None,
                open_url=open_url,
                open_title=open_title,
                open_blocked=open_blocked,
                open_block_reason=open_block_reason,
                search_blocked=search_blocked,
                search_block_reason=search_block_reason,
                area_confirmed=False,
                segment_code="",
                area_url=area_url,
                area_prices_count=area_prices_count,
                quote_avg=quote_avg,
                detail_clicked=False,
                detail_file=None,
                detail_url="",
                detail_title=None,
                community_avg_price=None,
                deal_clicked=False,
                deal_file=None,
                deal_url="",
                all_deals_count=0,
                filtered_deals_count=0,
                deal_avg=None,
                body_len=body_len,
                conclusion=f"search-only: 分页 {len(page_counts)} 页，合计在售 {area_prices_count} 条，均价 {format_price(quote_avg)}",
            )
            await wait_for_manual_close()
            return

        # ---- 第3步：面积筛选 ----
        if not search_blocked:
            log.info("[3] 填写面积筛选: %d-%d", AREA_MIN, AREA_MAX)
            area_confirmed, segment_code = await apply_area_filter(page, AREA_MIN, AREA_MAX)
            area_file = await dump_html(page, "lj_after_area")
            area_url = page.target.url or ""
            area_html = await page.get_content()
            area_prices_count = area_html.count("元/㎡")
            log.info("[3] 面积档位: %s, 筛选后 URL: %s", segment_code, area_url)

            # ---- 第4步：解析在售房源（链家和贝壳同代码库，DOM 一致）----
            listing_snapshots = parse_listing_snapshots(area_html)
            quote_prices = [s.unit_price for s in listing_snapshots if s.unit_price]
            quote_avg = sum(quote_prices) / len(quote_prices) if quote_prices else None
            area_prices_count = len(quote_prices)
            log.info("[4] 在售房源: %d 条, 在售均价 %s", area_prices_count,
                     f"{quote_avg:.2f}" if quote_avg else "None")

            # ---- 第5步：点开小区详情（新标签，参考贝壳）----
            if quote_prices:
                log.info("[5] 点击查看小区详情")
                detail_clicked, detail_tab = await click_detail_link(browser, page)
                if detail_clicked and detail_tab is not None:
                    await detail_tab
                    await asyncio.sleep(3)
                    detail_file = await dump_html(detail_tab, "lj_detail")
                    detail_url = detail_tab.target.url or ""
                    try:
                        detail_title = await detail_tab.evaluate("document.title", return_by_value=True)
                    except Exception:
                        detail_title = None
                    log.info("[5] 详情页 URL: %s", detail_url)

                    # ---- 第5步续：抓小区均价（xiaoquUnitPrice）----
                    detail_html = await detail_tab.get_content()
                    avg_m = re.search(r'<span class="xiaoquUnitPrice">([\d,]+)</span>', detail_html)
                    community_avg_price = float(avg_m.group(1).replace(",", "")) if avg_m else None
                    log.info("[5] 小区均价(xiaoquUnitPrice): %s", community_avg_price)

                    # ---- 第6步：点"查看全部成交记录"→成交列表页，翻页抓取+过滤----
                    log.info("[6] 点击查看全部成交记录")
                    deal_clicked = False
                    try:
                        deal_link = await detail_tab.select("a.btn-large", timeout=4)
                    except Exception:
                        deal_link = None
                    if deal_link:
                        old_tab_ids = {id(tab) for tab in browser.tabs}
                        if await human_click(detail_tab, deal_link, "查看全部成交记录"):
                            deal_tab = await wait_for_new_tab(browser, old_tab_ids, "/chengjiao/")
                            if deal_tab is not None:
                                deal_clicked = True
                                await deal_tab
                                await asyncio.sleep(3)
                                deal_file = await dump_html(deal_tab, "lj_deal")
                                deal_url = deal_tab.target.url or ""
                                log.info("[6] 成交页 URL: %s", deal_url)

                                # 翻页抓取成交记录
                                first_deal_html = await deal_tab.get_content()
                                total_deal_pages = parse_deal_total_pages(first_deal_html)
                                log.info("[6] 成交页总页数: %d", total_deal_pages)

                                all_deals: list = []
                                for deal_page_no in range(1, total_deal_pages + 1):
                                    if deal_page_no > 1:
                                        try:
                                            page_html = await click_deal_page_number(deal_tab, deal_page_no)
                                            await dump_html(deal_tab, f"lj_deal_page_{deal_page_no}")
                                        except Exception as exc:
                                            log.warning("[6] 翻到第 %d 页失败: %s", deal_page_no, exc)
                                            break
                                    else:
                                        page_html = first_deal_html

                                    page_deals = parse_deal_records(page_html)
                                    all_deals.extend(page_deals)
                                    log.info("[6] 第 %d 页成交: %d 条, 累计 %d 条", deal_page_no, len(page_deals), len(all_deals))

                                    # 优化：如果本页最新日期已超出近半年，后续页更旧，可以停
                                    if page_deals:
                                        newest = max(d[1] for d in page_deals if d[1])
                                        from datetime import datetime, timedelta
                                        cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
                                        oldest = min(d[1] for d in page_deals if d[1])
                                        if oldest < cutoff:
                                            log.info("[6] 第 %d 页已有超出半年的记录，停止翻页", deal_page_no)
                                            break

                                # 过滤：严格面积 + 近半年
                                filtered_deals = filter_deal_records(all_deals, AREA_MIN, AREA_MAX, months=6)
                                deal_prices = [d[3] for d in filtered_deals if d[3] is not None]
                                deal_avg = sum(deal_prices) / len(deal_prices) if deal_prices else None
                                log.info(
                                    "[6] 成交记录: 总 %d 条, %d-%d㎡且近半年 %d 条, 成交均价 %s",
                                    len(all_deals), AREA_MIN, AREA_MAX, len(filtered_deals),
                                    f"{deal_avg:.2f}" if deal_avg else "None",
                                )
                                print()
                                print("-" * 60)
                                print(f"成交记录（{AREA_MIN}-{AREA_MAX}㎡，近半年）共 {len(filtered_deals)} 条：")
                                for area, date_str, total, price in filtered_deals:
                                    print(f"  {area}㎡ {date_str} {total}万 {price}元/平")
                                if deal_avg:
                                    print(f"成交均价: {deal_avg:.2f} 元/平")
                                print("-" * 60)
                    if not deal_clicked:
                        log.warning("[6] 未能打开成交记录页")
                else:
                    log.warning("[5] 未能打开小区详情页")

            # ---- 算最终价：在售均价 vs 成交均价 ----
            if quote_avg is not None or deal_avg is not None:
                decision = decide(
                    quote_avg=quote_avg,
                    deal_avg=deal_avg,
                    diff_threshold=config.DEAL_DIFF_THRESHOLD,
                    no_deal_discount=config.NO_DEAL_DISCOUNT,
                )
                final_price = decision.final_price
                print()
                print("=" * 60)
                print("链家 平台结果")
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
        if open_blocked and not manual_login:
            conclusion = f"首次被拦：{open_block_reason}，建议加 --manual-login。"
        elif search_blocked:
            conclusion = f"搜索后被拦：{search_block_reason}。"
        elif area_confirmed:
            conclusion = f"搜索+面积筛选成功：{COMMUNITY_NAME} {AREA_MIN}-{AREA_MAX}㎡ 档位 {segment_code}，在售 {area_prices_count} 条。"
        else:
            conclusion = "面积筛选未能成功，需查看 HTML。"

        print_summary(
            open_file=open_file,
            search_after_file=search_after_file,
            area_file=area_file,
            error_file=None,
            open_url=open_url,
            open_title=open_title,
            open_blocked=open_blocked,
            open_block_reason=open_block_reason,
            search_blocked=search_blocked,
            search_block_reason=search_block_reason,
            area_confirmed=area_confirmed,
            segment_code=segment_code,
            area_url=area_url,
            area_prices_count=area_prices_count,
            quote_avg=quote_avg,
            detail_clicked=detail_clicked,
            detail_file=detail_file,
            detail_url=detail_url,
            detail_title=detail_title,
            community_avg_price=community_avg_price,
            deal_clicked=deal_clicked,
            deal_file=deal_file,
            deal_url=deal_url,
            all_deals_count=len(all_deals),
            filtered_deals_count=len(filtered_deals),
            deal_avg=deal_avg,
            body_len=body_len,
            conclusion=conclusion,
        )

        await wait_for_manual_close()
    except Exception:
        error_file = None
        if page is not None:
            error_file = await dump_html(page, "lj_error")
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
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="只测试搜索+在售分页，不做面积筛选和小区详情。",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(main(manual_login=args.manual_login, debug=args.debug, search_only=args.search_only))


if __name__ == "__main__":
    cli()
