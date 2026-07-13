# -*- coding: utf-8 -*-
"""乐有家 MVP 测试脚本。

逐步迭代：在同一脚本里一步步验证乐有家链路，不另建脚本。
当前覆盖：完整采集链路。

乐有家二手房：https://{城市拼音}.leyoujia.com/esf/

平台特性：
  - 搜索结果页有社区信息卡，含"小区均价"
  - 无成交记录（同安居客），业务上用小区均价顶替 deal_prices
  - 有分页：/esf/n{page}/?c={community}，走真实点击翻页
  - 房源列表在 ul.sort 下的 li 中，单价在 p.sub 里
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

import nodriver as uc

import config
from app.algorithm import decide
from app.debug_utils import dump_html as shared_dump_html
from app.debug_utils import set_debug_mode
from app.models import ListingSnapshot
from app.price_utils import format_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("lyj-mvp-test")

# 乐有家深圳二手房首页
START_URL = "https://shenzhen.leyoujia.com/esf/"

# 固定测试场景
COMMUNITY_NAME = "春华四季园"
AREA_MIN = 70
AREA_MAX = 90

PAGE_LINGER_SECONDS = config.PAGE_LINGER_SECONDS


# ============================================================
# 通用：HTML 导出 / 拦截判定 / 人工等待
# ============================================================

async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def is_captcha_url(url: str) -> bool:
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
# 页面交互辅助
# ============================================================

async def is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    import random
    await asyncio.sleep(random.uniform(min_s, max_s))


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
# 搜索
# ============================================================

async def _search_community(page, community_name: str) -> str:
    """搜索小区，返回结果页 HTML。
    乐有家搜索走 URL 参数：https://shenzhen.leyoujia.com/esf/?c={community}
    """
    search_url = f"https://shenzhen.leyoujia.com/esf/?c={community_name}"
    log.info("[2] 搜索 URL: %s", search_url)
    await page.get(search_url)
    await page
    await asyncio.sleep(3)
    return await page.get_content()


# ============================================================
# 面积筛选
# ============================================================

async def fill_area_inputs(page, area_min, area_max):
    """乐有家面积筛选：找到"面积"区 → 点"更多及自定义" → 填值 → 点确定。"""
    try:
        containers = await page.select_all("div.selected-index.hasmore", timeout=3)
    except Exception:
        containers = []

    area_container = None
    for c in containers:
        try:
            tit = await c.apply(
                "(el) => { const t = el.querySelector('.c333.tit'); return t ? t.textContent.trim() : ''; }"
            )
        except Exception:
            tit = ""
        if tit == "面积":
            area_container = c
            break

    if area_container is None:
        raise RuntimeError("未找到面积筛选区（标题为'面积'的 hasmore 容器）")

    try:
        btns = await area_container.query_selector_all("span.btn-showmore")
    except Exception:
        btns = []
    if btns:
        log.info("[3] 点击面积区的'更多及自定义'展开")
        await human_click(page, btns[0], "btn-showmore")
        await page
        await asyncio.sleep(2)

    try:
        min_el = await page.select("#a_start", timeout=3)
    except Exception:
        min_el = None
    try:
        max_el = await page.select("#a_end", timeout=3)
    except Exception:
        max_el = None

    if min_el is None or max_el is None:
        log.warning("[3] 展开后未找到 #a_start / #a_end")
        return False

    min_ok = await is_interactable(min_el)
    max_ok = await is_interactable(max_el)
    log.info("[3] 面积输入框: #a_start 可交互=%s, #a_end 可交互=%s", min_ok, max_ok)

    if not min_ok and not max_ok:
        log.warning("[3] 面积输入框不可交互，可能展开失败")
        return False

    if min_ok:
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

    if max_ok:
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

    try:
        confirm_btn = await page.select("#areaUsedefinedBtn", timeout=3)
    except Exception:
        confirm_btn = None

    confirm_clicked = False
    if confirm_btn:
        confirm_clicked = await human_click(page, confirm_btn, "area confirm")
    if not confirm_clicked and max_el and max_ok:
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
# HTML 解析：房源快照
# ============================================================

def _normalize_text(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_listing_snapshots(html: str) -> list[ListingSnapshot]:
    """从乐有家搜索结果页提取房源快照。

    每个房源在 <li class="item clearfix"> 内：
      p.tit a              → 标题
      p.attr span          → "3室2厅1卫 / 建筑面积73.5㎡"
      p.attr a[href*="/xq/detail"]  → 小区名链接
      span.salePrice       → 总价数字
      p.sub                → "单价44218元/㎡"
    排除猜你喜欢/推荐位 — 截断到"猜你喜欢"之前。
    """
    # 截断到尾页/猜你喜欢之前
    cut = html.find("猜你喜欢")
    source = html[:cut] if cut > 0 else html

    snapshots = []
    # 匹配 <li class="item clearfix" ...> ... </li>
    for block in re.finditer(
        r'<li class="item clearfix"[^>]*>(.*?)</li>', source, re.S
    ):
        chunk = block.group(1)

        # 小区名：p.attr 中的 a（/xq/detail/xxx 或带 font 包裹）
        community_name = None
        comm_m = re.search(r'href="/xq/detail/\d+[^"]*"[^>]*>(?:<[^>]+>)*\s*([^<]+)', chunk)
        if comm_m:
            community_name = _normalize_text(comm_m.group(1))

        # 户型：p.attr 中的第一组数字室数字厅
        layout = None
        layout_m = re.search(r"(\d+室\d+厅)", chunk)
        if layout_m:
            layout = layout_m.group(1)

        # 面积：建筑面积XX.XX㎡
        area = None
        area_m = re.search(r"建筑面积\s*([\d.]+)\s*㎡", chunk)
        if area_m:
            area = float(area_m.group(1))

        # 总价(万): span.salePrice
        total_price = None
        tp_m = re.search(r'salePrice[^>]*>\s*([\d,]+)\s*<', chunk)
        if tp_m:
            total_price = float(tp_m.group(1).replace(",", ""))

        # 单价: p.sub 中的 "单价44218元/㎡"
        unit_price = None
        up_m = re.search(
            r'<p class="sub">.*?([\d,]+)\s*元\s*/?\s*㎡',
            chunk,
        )
        if up_m:
            unit_price = float(up_m.group(1).replace(",", ""))

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


# ============================================================
# HTML 解析：小区均价（社区信息卡）
# ============================================================

def parse_community_avg_price(html: str) -> Optional[float]:
    """从结果页社区信息卡提取小区均价。

    DOM: <em class="txt">54386元/㎡</em>
    乐有家无成交记录，业务上用小区均价顶替 deal_prices。
    """
    m = re.search(r"小区均价</em>\s*<em\s[^>]*>\s*([\d,]+)\s*元", html)
    return float(m.group(1).replace(",", "")) if m else None


# ============================================================
# 分页解析与导航
# ============================================================

def _parse_current_page(html: str) -> int:
    """解析当前页码。

    DOM: <a class="on" href="...">1</a>
    """
    m = re.search(r'<a[^>]*class="on"[^>]*href="[^"]*">(\d+)</a>', html or "")
    return int(m.group(1)) if m else 1


def _parse_total_pages(html: str) -> int:
    """解析总页数。

    尾页链接: <a href="/esf/n{page}/?c=..." title="{page}">尾页</a>
    """
    m = re.search(r'<a[^>]*title="(\d+)"[^>]*>尾页</a>', html or "")
    return int(m.group(1)) if m else 1


async def _wait_for_results_loaded(page, expected_page: int, timeout: float = 15) -> str:
    """等待结果页加载完成，返回 HTML。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last_html = ""
    while asyncio.get_event_loop().time() < deadline:
        last_html = await page.get_content()
        if _parse_current_page(last_html) == expected_page:
            await asyncio.sleep(1.2)
            return last_html
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.2)
    return last_html or await page.get_content()


async def _click_page_number(page, page_no: int) -> str:
    """点击乐有家页码按钮，返回加载完成后的 HTML。

    页码链接: <a href="/esf/n{page}/?c=..." title="{page}">2</a>
    """
    try:
        elements = await page.select_all(
            f'a[title="{page_no}"]', timeout=3
        )
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

    return await _wait_for_results_loaded(page, expected_page=page_no)


async def _human_linger_on_result_page(page, page_no: int, linger_seconds: float = PAGE_LINGER_SECONDS):
    """真人式滚动停留。"""
    start_val = time.monotonic()
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

    remain = linger_seconds - (time.monotonic() - start_val)
    if remain > 0:
        await asyncio.sleep(remain)


async def _collect_listing_pages(page, first_page_html: str, total_pages: int):
    """逐页采集在售房源快照。

    乐有家分页：URL 格式 /esf/n{page}/?c={community}+面积筛选参数，
    按页码锚点直接点击翻页。
    """
    all_snapshots: list[ListingSnapshot] = []
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            last_html = await _click_page_number(page, page_no)

        await _human_linger_on_result_page(page, page_no)
        last_html = await page.get_content()
        await dump_html(page, f"lyj_area_page_{page_no}")

        page_snapshots = parse_listing_snapshots(last_html)
        all_snapshots.extend(page_snapshots)
        log.info(
            "[4] 第 %d/%d 页: %d 条", page_no, total_pages, len(page_snapshots)
        )

    return all_snapshots, last_html


# ============================================================
# 打印房源摘要（对齐安居客打印格式）
# ============================================================

def print_listing_snapshots(snapshots: list[ListingSnapshot]):
    if not snapshots:
        print("乐有家: 未抓到房源摘要")
        return
    for item in snapshots:
        print(
            "乐有家: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )


# ============================================================
# 主流程
# ============================================================

def print_summary(
    *,
    open_file: Optional[Path],
    search_after_file: Optional[Path],
    area_files: list,
    error_file: Optional[Path],
    open_url: str,
    open_title: Optional[str],
    open_blocked: bool,
    open_block_reason: Optional[str],
    search_blocked: bool,
    search_block_reason: Optional[str],
    search_no_data: bool,
    result_url: str,
    result_title: Optional[str],
    area_confirmed: bool,
    area_url: str,
    total_pages: int,
    quote_prices_count: int,
    main_listing_prices: list,
    listing_avg: Optional[float],
    listing_price: Optional[float],
    deal_avg: Optional[float],
    final_price: Optional[float],
    branch: str,
    body_len: Optional[int],
    conclusion: str,
):
    print()
    print("=" * 60)
    print("乐有家测试完成")
    print(f"打开首页 HTML: {open_file}")
    print(f"搜索后 HTML: {search_after_file}")
    print(f"面积筛选后分页 HTML: {area_files}")
    print(f"异常现场 HTML: {error_file}")
    print(f"首次打开 URL: {open_url}")
    print(f"首次打开标题: {open_title}")
    print(f"首次是否被拦: {open_blocked}")
    print(f"首次拦截原因: {open_block_reason}")
    print(f"搜索后是否被拦: {search_blocked}")
    print(f"搜索后拦截原因: {search_block_reason}")
    print(f"搜索无数据: {search_no_data}")
    print(f"结果页 URL: {result_url}")
    print(f"结果页标题: {result_title}")
    print(f"面积筛选点击: {area_confirmed}")
    print(f"面积筛选后 URL: {area_url}")
    print(f"总页数: {total_pages}")
    print(f"主结果区在售单价数: {quote_prices_count}")
    if listing_avg is not None:
        print(f"主结果区在售均价(quoteAvg): {listing_avg:.2f} 元/㎡")
    if listing_price is not None:
        print(f"小区均价(顶替成交 dealAvg): {listing_price:.2f} 元/㎡")
    print(f"最终取值(finalPrice): {format_price(final_price)}")
    print(f"决策分支(branch): {branch}")
    print(f"正文长度: {body_len}")
    print(f"结论: {conclusion}")
    print("=" * 60)
    print()


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
    open_url = ""
    open_title: Optional[str] = None
    open_blocked = False
    open_block_reason: Optional[str] = None
    body_len: Optional[int] = None
    search_after_file = None
    search_blocked = False
    search_block_reason: Optional[str] = None
    search_no_data = False
    result_url = ""
    result_title: Optional[str] = None
    area_file = None
    area_confirmed = False
    area_url = ""
    total_pages = 0
    area_files: list = []
    listing_snapshots: list[ListingSnapshot] = []
    listing_avg: Optional[float] = None
    listing_price: Optional[float] = None
    deal_avg: Optional[float] = None
    final_price: Optional[float] = None
    branch = ""

    try:
        # ---- 第1步：打开首页 ----
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        open_file = await dump_html(page, "lyj_opened")

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

        # ---- 人工处理 ----
        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(START_URL)
            await page
            await asyncio.sleep(3)
            open_file = await dump_html(page, "lyj_reopened")
            open_url = page.target.url or ""
            open_html = await page.get_content()
            open_blocked = is_captcha_url(open_url) or is_captcha_html(open_html) or is_login_html(open_html)
            if open_blocked:
                open_block_reason = "重开后仍被拦"
            try:
                open_title = await page.evaluate("document.title", return_by_value=True)
                body_len = await page.evaluate("document.body.innerText.length", return_by_value=True)
            except Exception:
                pass

        # ---- 第2步：搜索小区 ----
        if not open_blocked:
            log.info("[2] 搜索小区: %s", COMMUNITY_NAME)
            try:
                result_html = await _search_community(page, COMMUNITY_NAME)
                search_after_file = await dump_html(page, "lyj_search_after")
                result_url = page.target.url or ""

                if is_captcha_url(result_url) or is_captcha_html(result_html):
                    search_blocked = True
                    search_block_reason = "搜索后命中验证码拦截"
                elif is_login_html(result_html):
                    search_blocked = True
                    search_block_reason = "搜索后进入登录页"
                elif "很抱歉，没有找到" in result_html:
                    search_no_data = True
                    log.info("[2] 搜索成功但无匹配结果: %s", COMMUNITY_NAME)
                else:
                    log.info("[2] 搜索成功: %s", COMMUNITY_NAME)

                try:
                    result_title = await page.evaluate("document.title", return_by_value=True)
                except Exception:
                    pass
            except Exception as exc:
                log.warning("[2] 搜索异常: %s", exc)
                search_blocked = True
                search_block_reason = f"搜索异常: {exc}"

        # ---- 第3步：面积自定义筛选 ----
        if not open_blocked and not search_blocked and not search_no_data:
            log.info("[3] 填写面积筛选: %d-%d", AREA_MIN, AREA_MAX)
            try:
                area_confirmed = await fill_area_inputs(page, AREA_MIN, AREA_MAX)
            except Exception as exc:
                log.warning("[3] 面积筛选异常: %s", exc)
                area_confirmed = False

            area_file = await dump_html(page, "lyj_after_area")
            area_url = page.target.url or ""
            area_html = await page.get_content()
            if is_captcha_url(area_url) or is_captcha_html(area_html):
                search_blocked = True
                search_block_reason = "面积筛选后命中验证码拦截"

        # ---- 第4步：分页采集 + 解析房源快照 ----
        if not open_blocked and not search_blocked and not search_no_data and area_confirmed:
            log.info("[4] 开始分页采集")
            area_html = await page.get_content()
            total_pages = _parse_total_pages(area_html)
            log.info("[4] 总页数: %d", total_pages)

            try:
                listing_snapshots, last_page_html = await _collect_listing_pages(
                    page, area_html, total_pages
                )
            except Exception as exc:
                log.warning("[4] 分页采集异常: %s", exc)
                listing_snapshots = parse_listing_snapshots(area_html)

            log.info("[4] 共采集 %d 条房源快照", len(listing_snapshots))

            # 打印房源摘要（对齐安居客格式）
            print()
            print("-" * 60)
            print_listing_snapshots(listing_snapshots)
            print("-" * 60)

            # ---- 第5步：小区均价 + 最终价计算 ----
            quote_prices = [s.unit_price for s in listing_snapshots if s.unit_price]
            listing_price = parse_community_avg_price(area_html)

            if quote_prices:
                listing_avg = sum(quote_prices) / len(quote_prices)

            # 乐有家无成交记录，小区均价顶替 deal_prices（同安居客处理）
            deal_prices = [listing_price] if listing_price is not None else []
            deal_avg = listing_price

            decision = decide(
                quote_avg=listing_avg,
                deal_avg=deal_avg,
                diff_threshold=config.DEAL_DIFF_THRESHOLD,
                no_deal_discount=config.NO_DEAL_DISCOUNT,
            )
            final_price = decision.final_price
            branch = decision.branch

            log.info(
                "[5] 在售均价=%.2f 小区均价(顶替成交)=%s 最终价=%.2f 分支=%s",
                listing_avg or 0,
                listing_price,
                final_price or 0,
                branch,
            )

        # 判定结论
        if open_blocked and not manual_login:
            conclusion = f"首次被拦：{open_block_reason}，建议加 --manual-login。"
        elif open_blocked:
            conclusion = f"人工处理后仍被拦：{open_block_reason}。"
        elif search_blocked:
            conclusion = f"搜索后被拦：{search_block_reason}。"
        elif search_no_data:
            conclusion = f"搜索成功但无匹配结果：{COMMUNITY_NAME} 在乐有家无在售记录。"
        elif listing_snapshots:
            snap_count = len(listing_snapshots)
            quote_count = len([s for s in listing_snapshots if s.unit_price])
            conclusion = (
                f"采集成功：{COMMUNITY_NAME} {AREA_MIN}-{AREA_MAX}㎡ 共 {snap_count} 条，"
                f"在售均价 {format_price(listing_avg)} 元/㎡，"
                f"小区均价 {format_price(listing_price)} 元/㎡（顶替成交），"
                f"最终价 {format_price(final_price)} 元/㎡（{branch}）"
            )
        elif area_confirmed:
            conclusion = f"面积筛选成功但未抓到在售单价，总页数={total_pages}，需查看 HTML。"
        elif result_url:
            conclusion = f"搜索成功但面积筛选未提交，需查看 HTML。"
        else:
            conclusion = "流程异常，需查看 HTML 排查。"

        quote_prices_count = len([s for s in listing_snapshots if s.unit_price])
        main_listing_prices = [s.unit_price for s in listing_snapshots if s.unit_price]
        print_summary(
            open_file=open_file,
            search_after_file=search_after_file,
            area_files=[str(f) for f in area_files if f] if area_files else [],
            error_file=None,
            open_url=open_url,
            open_title=open_title,
            open_blocked=open_blocked,
            open_block_reason=open_block_reason,
            search_blocked=search_blocked,
            search_block_reason=search_block_reason,
            search_no_data=search_no_data,
            result_url=result_url,
            result_title=result_title,
            area_confirmed=area_confirmed,
            area_url=area_url,
            total_pages=total_pages,
            quote_prices_count=quote_prices_count,
            main_listing_prices=main_listing_prices,
            listing_avg=listing_avg,
            listing_price=listing_price,
            deal_avg=deal_avg,
            final_price=final_price,
            branch=branch,
            body_len=body_len,
            conclusion=conclusion,
        )

        await wait_for_manual_close()
    except Exception:
        error_file = None
        if page is not None:
            error_file = await dump_html(page, "lyj_error")
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
