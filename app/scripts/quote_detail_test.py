# -*- coding: utf-8 -*-
"""在售单价 + 小区详情测试脚本。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from pathlib import Path
from statistics import mean
from typing import Optional

import nodriver as uc

from app.core import config
from app.parsers import ke as parsers
from app.utils.debug_utils import dump_html as shared_dump_html
from app.utils.debug_utils import set_debug_mode
from app.platforms.adapters.ke import _get_search_input, _human_click, _submit_search
from app.platforms.ke_constants import START_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("quote-detail-test")

COMMUNITY_NAME = "绿景虹湾"
AREA_MIN = 70.0
AREA_MAX = 90.0
AREA_SEGMENT = "a3"
PAGE_LINGER_SECONDS = config.PAGE_LINGER_SECONDS


async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url or "clogin.ke.com" in url


def is_login_html(html: str) -> bool:
    markers = (
        'meta name="ke-passport" content="LOGIN"',
        'id="login"',
        "请输入手机号",
        "请输入密码",
        "手机快捷登录",
    )
    return any(marker in html for marker in markers)


def fmt_float(value):
    return round(value, 2) if value is not None else None


def parse_total_count(html: str) -> Optional[int]:
    match = re.search(r"共找到\s*<span>\s*(\d+)\s*</span>", html or "")
    return int(match.group(1)) if match else None


def parse_total_pages(html: str) -> Optional[int]:
    match = re.search(
        r'page-data="\{&quot;totalPage&quot;:(\d+),&quot;curPage&quot;:(\d+)\}"',
        html or "",
    )
    return int(match.group(1)) if match else None


def parse_current_page(html: str) -> Optional[int]:
    match = re.search(
        r'page-data="\{&quot;totalPage&quot;:(\d+),&quot;curPage&quot;:(\d+)\}"',
        html or "",
    )
    return int(match.group(2)) if match else None


def parse_pagination_anchor_pages(html: str) -> list[int]:
    pages = {int(page_no) for page_no in re.findall(r'data-page="(\d+)"', html or "")}
    return sorted(pages)


def parse_pagination_url_template(html: str) -> Optional[str]:
    match = re.search(r'page-url="([^"]+)"', html or "")
    return match.group(1) if match else None


def extract_xiaoqu_id(detail_url: Optional[str]) -> Optional[str]:
    if not detail_url:
        return None
    match = re.search(r"/xiaoqu/(\d+)/", detail_url)
    return match.group(1) if match else None


async def wait_for_manual_login():
    prompt = (
        "\n请在打开的浏览器里手动完成人机登录。"
        "\n登录完成并回到二手房页面后，在终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


async def wait_for_manual_close():
    prompt = (
        "\n浏览器将保持打开，方便你现场查看。"
        "\n请手动关闭浏览器窗口；看完后回到终端按回车结束脚本...\n"
    )
    await asyncio.to_thread(input, prompt)


async def wait_for_results_loaded(page, expected_page: Optional[int] = None) -> str:
    await page.select("ul.sellListContent", timeout=15)
    await page

    last_html = ""
    for _ in range(20):
        last_html = await page.get_content()
        if expected_page is None or parse_current_page(last_html) == expected_page:
            await asyncio.sleep(1.2)
            return last_html
        await asyncio.sleep(0.5)

    await asyncio.sleep(1.2)
    return last_html or await page.get_content()


async def human_linger_on_result_page(page, page_no: int, linger_seconds: float = PAGE_LINGER_SECONDS):
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


async def wait_for_new_tab(browser, old_tab_ids: set[int], expected_url: str | None):
    for _ in range(20):
        await asyncio.sleep(0.5)
        for tab in browser.tabs:
            if id(tab) not in old_tab_ids:
                return tab
            if expected_url and (tab.target.url or "").startswith(expected_url):
                return tab
    return None


async def apply_area_filter_on_result_page(page, community_name: str):
    expected_href = f"/ershoufang/{AREA_SEGMENT}rs{community_name}"
    selectors = [
        f"a[href='{expected_href}']",
        f"a[href^='/ershoufang/{AREA_SEGMENT}rs']",
    ]

    for selector in selectors:
        try:
            element = await page.select(selector, timeout=2)
        except Exception:
            element = None
        if element and await _human_click(page, element, "result area segment"):
            html = await wait_for_results_loaded(page, expected_page=1)
            return page, html, "click", page.target.url or ""

    raise RuntimeError("结果页未找到可点击的面积筛选项")


async def click_page_number(page, page_no: int):
    selector = f".house-lst-page-box a[data-page='{page_no}']"
    try:
        element = await page.select(selector, timeout=3)
    except Exception:
        element = None

    if not element:
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")

    if not await _human_click(page, element, f"page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")

    return await wait_for_results_loaded(page, expected_page=page_no)


async def collect_listing_pages(
    page,
    first_page_html: str,
    total_pages: int,
    dump_prefix: str,
):
    all_records: dict[str, float] = {}
    page_counts: list[tuple[int, int]] = []
    page_files: list[Optional[Path]] = []
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            last_html = await click_page_number(page, page_no)

        await human_linger_on_result_page(page, page_no)
        last_html = await page.get_content()
        page_file = await dump_html(page, f"{dump_prefix}_{page_no}")
        page_files.append(page_file)

        page_records = parsers.parse_listing_records(last_html)
        page_counts.append((page_no, len(page_records)))
        for house_id, price in page_records:
            all_records[house_id] = price

    return all_records, page_counts, page_files, last_html


async def exercise_keyword_pagination_flow(page, keyword_html: str, keyword_total_pages: int):
    if keyword_total_pages <= 1:
        return {}, [], [], keyword_html

    log.info("exercising keyword result pagination: total_pages=%s", keyword_total_pages)
    listing_map, page_counts, page_files, last_html = await collect_listing_pages(
        page,
        keyword_html,
        keyword_total_pages,
        dump_prefix="quote_detail_keyword_page",
    )

    if keyword_total_pages > 1:
        await click_page_number(page, 1)
        await asyncio.sleep(1.0)

    return listing_map, page_counts, page_files, last_html


async def click_detail_link(browser, page, expected_url: Optional[str]):
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

    clicked = await _human_click(page, detail_link, "detail link")
    if not clicked:
        return False, None

    detail_tab = await wait_for_new_tab(browser, old_tab_ids, expected_url)
    if detail_tab:
        return True, detail_tab

    current_url = page.target.url or ""
    if expected_url and current_url.startswith(expected_url):
        return True, page

    return True, None


def print_summary(
    *,
    open_file: Optional[Path],
    login_file: Optional[Path],
    keyword_file: Optional[Path],
    keyword_page_files: list[Optional[Path]],
    filtered_file: Optional[Path],
    page_files: list[Optional[Path]],
    detail_file: Optional[Path],
    error_file: Optional[Path],
    keyword_url: str,
    keyword_total_count: Optional[int],
    keyword_total_pages: Optional[int],
    keyword_anchor_pages: list[int],
    keyword_page_url_template: Optional[str],
    keyword_page_counts: list[tuple[int, int]],
    filtered_url: str,
    filtered_total_count: Optional[int],
    filtered_total_pages: Optional[int],
    filtered_anchor_pages: list[int],
    filtered_page_url_template: Optional[str],
    filter_apply_mode: str,
    page_counts: list[tuple[int, int]],
    listing_prices: list[float],
    detail_url: Optional[str],
    xiaoqu_id: Optional[str],
    detail_clicked: bool,
    detail_tab_url: Optional[str],
    community_avg_price: Optional[float],
    sold_list_url: Optional[str],
    deal_records_count: int,
    filtered_deal_prices: list[float],
):
    listing_avg = mean(listing_prices) if listing_prices else None
    deal_avg = mean(filtered_deal_prices) if filtered_deal_prices else None

    print()
    print("=" * 60)
    print("单价与详情流程测试完成")
    print(f"打开首页 HTML: {open_file}")
    print(f"登录后 HTML: {login_file}")
    print(f"关键词结果 HTML: {keyword_file}")
    print(f"关键词分页 HTML: {[str(path) if path else None for path in keyword_page_files]}")
    print(f"面积结果首屏 HTML: {filtered_file}")
    print(f"分页 HTML: {[str(path) if path else None for path in page_files]}")
    print(f"详情页 HTML: {detail_file}")
    print(f"异常现场 HTML: {error_file}")
    print(f"关键词结果 URL: {keyword_url}")
    print(f"关键词结果总数: {keyword_total_count}")
    print(f"关键词结果总页数: {keyword_total_pages}")
    print(f"关键词分页锚点: {keyword_anchor_pages}")
    print(f"关键词分页模板: {keyword_page_url_template}")
    print(f"关键词分页每页主结果数量: {keyword_page_counts}")
    print(f"面积结果 URL: {filtered_url}")
    print(f"面积筛选落地方式: {filter_apply_mode}")
    print(f"面积结果总数: {filtered_total_count}")
    print(f"面积结果总页数: {filtered_total_pages}")
    print(f"面积分页锚点: {filtered_anchor_pages}")
    print(f"面积分页模板: {filtered_page_url_template}")
    print(f"每页主结果数量: {page_counts}")
    print(f"全量去重后在售单价数量: {len(listing_prices)}")
    print(f"全量去重后在售单价均值: {fmt_float(listing_avg)}")
    print(f"全量去重后在售单价前10条: {listing_prices[:10]}")
    print(f"小区详情链接: {detail_url}")
    print(f"小区 ID: {xiaoqu_id}")
    print(f"是否成功模拟点击详情: {detail_clicked}")
    print(f"详情页 URL: {detail_tab_url}")
    print(f"小区参考均价: {community_avg_price}")
    print(f"成交列表链接: {sold_list_url}")
    print(f"详情页成交记录数: {deal_records_count}")
    print(f"面积过滤后成交单价数: {len(filtered_deal_prices)}")
    print(f"面积过滤后成交均值: {fmt_float(deal_avg)}")
    print(f"面积过滤后成交单价: {filtered_deal_prices}")
    print("=" * 60)
    print()


async def main(manual_login: bool = False, exercise_keyword_pagination: bool = False, debug: bool = False):
    if debug:
        set_debug_mode(True)

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    page = None
    open_file = None
    login_file = None
    keyword_file = None
    keyword_page_files: list[Optional[Path]] = []
    filtered_file = None
    detail_file = None

    try:
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        open_file = await dump_html(page, "quote_detail_opened")

        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(START_URL)
            await page
            await asyncio.sleep(3)
            login_file = await dump_html(page, "quote_detail_after_manual_login")

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
        await inp.send_keys(COMMUNITY_NAME)
        await page
        await asyncio.sleep(1.0)

        await _submit_search(page, inp)
        keyword_html = await wait_for_results_loaded(page, expected_page=1)

        keyword_url = page.target.url or ""
        keyword_file = await dump_html(page, "quote_detail_keyword_result")

        if is_login_url(keyword_url) or is_login_html(keyword_html):
            raise RuntimeError("搜索后跳转到了登录页，请先人工登录后再运行")

        keyword_total_count = parse_total_count(keyword_html)
        keyword_total_pages = parse_total_pages(keyword_html) or 1
        keyword_anchor_pages = parse_pagination_anchor_pages(keyword_html)
        keyword_page_url_template = parse_pagination_url_template(keyword_html)
        detail_url = parsers.find_detail_link(keyword_html)
        xiaoqu_id = extract_xiaoqu_id(detail_url)
        if not detail_url:
            raise RuntimeError("关键词结果页未找到小区详情链接")

        keyword_page_counts: list[tuple[int, int]] = []
        if exercise_keyword_pagination and keyword_total_pages > 1:
            _, keyword_page_counts, keyword_page_files, _ = await exercise_keyword_pagination_flow(
                page,
                keyword_html,
                keyword_total_pages,
            )
            keyword_html = await wait_for_results_loaded(page, expected_page=1)
        else:
            keyword_page_counts = [(1, len(parsers.parse_listing_records(keyword_html)))]

        page, filtered_html, filter_apply_mode, filtered_url = await apply_area_filter_on_result_page(
            page,
            COMMUNITY_NAME,
        )
        filtered_file = await dump_html(page, "quote_detail_after_area_result")

        if is_login_url(filtered_url) or is_login_html(filtered_html):
            raise RuntimeError("结果页面积筛选后跳转到了登录页")

        filtered_total_count = parse_total_count(filtered_html)
        filtered_total_pages = parse_total_pages(filtered_html) or 1
        filtered_anchor_pages = parse_pagination_anchor_pages(filtered_html)
        filtered_page_url_template = parse_pagination_url_template(filtered_html)

        listing_map, page_counts, page_files, last_page_html = await collect_listing_pages(
            page,
            filtered_html,
            filtered_total_pages,
            dump_prefix="quote_detail_area_page",
        )
        listing_prices = list(listing_map.values())
        if not listing_prices:
            raise RuntimeError("面积结果页未抓到在售单价")

        detail_url = parsers.find_detail_link(last_page_html) or detail_url
        xiaoqu_id = extract_xiaoqu_id(detail_url) or xiaoqu_id

        detail_clicked, detail_tab = await click_detail_link(browser, page, detail_url)
        if not detail_clicked:
            raise RuntimeError("未能成功模拟点击小区详情")
        if detail_tab is None:
            raise RuntimeError("点击详情后未识别到详情页标签")

        await detail_tab
        await asyncio.sleep(3)

        detail_html = await detail_tab.get_content()
        detail_file = await dump_html(detail_tab, "quote_detail_after_detail_click")
        detail_tab_url = detail_tab.target.url or ""
        community_avg_price = parsers.parse_community_avg_price(detail_html)
        sold_list_url = parsers.find_sold_list_url(detail_html)
        deal_records = parsers.parse_deal_records(detail_html)
        filtered_deal_prices = parsers.filter_deal_prices_by_area(
            deal_records,
            AREA_MIN,
            AREA_MAX,
        )

        print_summary(
            open_file=open_file,
            login_file=login_file,
            keyword_file=keyword_file,
            keyword_page_files=keyword_page_files,
            filtered_file=filtered_file,
            page_files=page_files,
            detail_file=detail_file,
            error_file=None,
            keyword_url=keyword_url,
            keyword_total_count=keyword_total_count,
            keyword_total_pages=keyword_total_pages,
            keyword_anchor_pages=keyword_anchor_pages,
            keyword_page_url_template=keyword_page_url_template,
            keyword_page_counts=keyword_page_counts,
            filtered_url=filtered_url,
            filtered_total_count=filtered_total_count,
            filtered_total_pages=filtered_total_pages,
            filtered_anchor_pages=filtered_anchor_pages,
            filtered_page_url_template=filtered_page_url_template,
            filter_apply_mode=filter_apply_mode,
            page_counts=page_counts,
            listing_prices=listing_prices,
            detail_url=detail_url,
            xiaoqu_id=xiaoqu_id,
            detail_clicked=detail_clicked,
            detail_tab_url=detail_tab_url,
            community_avg_price=community_avg_price,
            sold_list_url=sold_list_url,
            deal_records_count=len(deal_records),
            filtered_deal_prices=filtered_deal_prices,
        )

        await wait_for_manual_close()
    except Exception:
        error_file = None
        if page is not None:
            error_file = await dump_html(page, "quote_detail_error")
        raise
    finally:
        browser.stop()


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="先人工登录，回车后再继续执行单价与详情测试。",
    )
    parser.add_argument(
        "--exercise-keyword-pagination",
        action="store_true",
        help="额外演练关键词结果页的真实翻页点击链路。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML 到 debug 目录。",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(
        main(
            manual_login=args.manual_login,
            exercise_keyword_pagination=args.exercise_keyword_pagination,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    cli()
