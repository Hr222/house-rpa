# -*- coding: utf-8 -*-
"""贝壳 MVP 测试脚本。

完整业务链路：打开首页 → 人工登录 → 搜索小区 → 面积筛选 →
分页翻页 → 在售解析 → 详情页 → 小区均价 → 成交记录 → 算法决策。

用法：
  python -m app.scripts.ke_mvp_test
  python -m app.scripts.ke_mvp_test --debug
  python -m app.scripts.ke_mvp_test --community "绿景虹湾" --min 70 --max 90
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

from app.core import config
from app.core.algorithm import decide, mean
from app.core.models import ListingSnapshot
from app.core.price_utils import format_price, round_price
from app.parsers import ke as parsers
from app.platforms.adapters.ke import (
    _get_search_input,
    _human_click,
    _submit_search,
)
from app.platforms.ke_constants import AREA_SEGMENTS, START_URL
from app.utils.debug_utils import dump_html as shared_dump_html
from app.utils.debug_utils import set_debug_mode
from app.utils.mvp_result import print_mvp_result
from app.utils.logging_utils import setup_logging

setup_logging()
log = logging.getLogger("ke-mvp-test")

# ---- 测试参数 ----

DEFAULT_COMMUNITY = "绿景虹湾"
DEFAULT_AREA_MIN = 70.0
DEFAULT_AREA_MAX = 90.0
PAGE_LINGER_SECONDS = config.PAGE_LINGER_SECONDS


# ============================================================
# 辅助函数
# ============================================================

async def _dump(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    import random
    await asyncio.sleep(random.uniform(min_s, max_s))


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


# ============================================================
# 分页解析
# ============================================================

def _parse_total_pages(html: str) -> int:
    m = re.search(
        r'page-data="\{&quot;totalPage&quot;:(\d+),&quot;curPage&quot;:(\d+)\}"',
        html or "",
    )
    return int(m.group(1)) if m else 1


def _parse_current_page(html: str) -> int:
    m = re.search(
        r'page-data="\{&quot;totalPage&quot;:(\d+),&quot;curPage&quot;:(\d+)\}"',
        html or "",
    )
    return int(m.group(2)) if m else 1


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


async def _human_linger_on_result_page(page, page_no: int, linger_seconds: float = PAGE_LINGER_SECONDS):
    log.info("第 %d 页真人滚动停留 %.1fs", page_no, linger_seconds)
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


# ============================================================
# 面积筛选（更多及自定义 → 填值 → 确定）
# ============================================================

async def _apply_area_filter(page, area_min, area_max):
    """贝壳面积筛选：智能展开 → 面积区"更多及自定义" → 填值 → 确定。

    贝壳首页：筛选区已展开，无需全局按钮
    搜索结果页：div.more.btn-more="更多选项"（需先点击展开）
    """
    # 1. 智能全局展开：只有"更多选项"才点
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
            log.info("[3] 点击全局'更多选项'展开")
            await _human_click(page, more_btn, "global btn-more")
            await page
            await asyncio.sleep(1.5)
        else:
            log.info("[3] 筛选区已展开(按钮=%s)，跳过全局点击", btn_text)

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
        raise RuntimeError("未找到建筑面积筛选区（dl.hide.hasmore dt[title*=建筑面积]）")

    # 2. 点击面积区内的 btn-showmore 展开
    try:
        btns = await area_container.query_selector_all("span.btn-showmore")
    except Exception:
        btns = []
    if btns:
        log.info("[3] 点击建筑面积区的 btn-showmore 展开")
        await _human_click(page, btns[0], "btn-showmore")
        await page
        await asyncio.sleep(1.5)

    # 3. 取自定义输入框
    try:
        custom = await area_container.query_selector_all("span.customFilter[data-role='area']")
    except Exception:
        custom = []
    if not custom:
        raise RuntimeError("未找到面积自定义输入区 customFilter[data-role=area]")

    min_el = None
    max_el = None
    try:
        min_el = await custom[0].query_selector_all("input[role='minValue']")
    except Exception:
        min_el = []
    try:
        max_el = await custom[0].query_selector_all("input[role='maxValue']")
    except Exception:
        max_el = []

    if not min_el or not max_el:
        raise RuntimeError("未找到面积自定义输入框")
    min_el, max_el = min_el[0], max_el[0]

    # 4. 填下限
    await _human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)
    log.info("[3] 填入下限: %s", area_min)

    # 5. 填上限
    await _human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)
    log.info("[3] 填入上限: %s", area_max)

    # 6. 点"确定"提交
    try:
        btns = await custom[0].query_selector_all("button.btn-range")
    except Exception:
        btns = []
    if not btns:
        raise RuntimeError("未找到面积确定按钮")

    confirm_clicked = False
    try:
        # 按钮可能为 hide 类，用 JS 点击兜底
        await _human_click(page, btns[0], "area confirm")
        confirm_clicked = True
    except Exception:
        pass
    if not confirm_clicked:
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
        confirm_clicked = True

    await page
    await asyncio.sleep(3)
    return await _wait_for_results_loaded(page, expected_page=1)


# ============================================================
# 详情页
# ============================================================

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


# ============================================================
# 登录交互
# ============================================================

async def _wait_for_manual_login():
    prompt = (
        "\n请在浏览器中手动完成贝壳的登录。"
        "\n登录完成并回到二手房首页后，在终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


async def _wait_for_manual_close():
    prompt = (
        "\n浏览器将保持打开，方便你现场查看。"
        "\n看完后回到终端按回车结束脚本...\n"
    )
    await asyncio.to_thread(input, prompt)


# ============================================================
# 采集主体
# ============================================================

async def run_ke_collect(
    browser,
    community_name: str,
    area_min: float,
    area_max: float,
    manual_login: bool = True,
) -> dict:
    """执行一次完整的贝壳询价采集，返回汇总 dict。"""
    started_at = time.time()

    # ---- 1. 打开首页 ----
    log.info("[1] 打开贝壳首页")
    page = await browser.get(START_URL)
    await page
    await asyncio.sleep(3)
    await _dump(page, "ke_home")

    # ---- 2. 人工登录 ----
    if manual_login:
        await _wait_for_manual_login()
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        await _dump(page, "ke_after_login")

    # ---- 3. 搜索小区 ----
    log.info("[2] 搜索小区: %s", community_name)
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

    keyword_html = await _wait_for_results_loaded(page, expected_page=1)
    keyword_url = page.target.url or ""
    await _dump(page, "ke_keyword_result")

    if _is_login_url(keyword_url) or _is_login_html(keyword_html):
        raise RuntimeError("搜索后跳转到了登录页，请确认已登录")

    detail_url = parsers.find_detail_link(keyword_html)
    if not detail_url:
        raise RuntimeError("关键词结果页未找到小区详情链接")

    # ---- 4. 面积筛选（更多及自定义）----
    log.info("[3] 面积筛选: %d-%d", area_min, area_max)
    filtered_html = await _apply_area_filter(page, area_min, area_max)
    await _dump(page, "ke_after_area")

    filtered_url = page.target.url or ""
    if _is_login_url(filtered_url) or _is_login_html(filtered_html):
        raise RuntimeError("面积筛选后跳转到登录页")

    total_pages = _parse_total_pages(filtered_html)
    log.info("[4] 总页数: %d", total_pages)

    all_listing_prices: list[float] = []
    all_snapshots: list[ListingSnapshot] = []

    # ---- 5. 分页采集在售 ----
    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            filtered_html = await _click_page_number(page, page_no)

        await _human_linger_on_result_page(page, page_no)
        filtered_html = await page.get_content()
        await _dump(page, f"ke_area_page_{page_no}")

        page_prices = parsers.parse_listing_prices(filtered_html)
        page_snapshots = parsers.parse_listing_snapshots(filtered_html)
        all_listing_prices.extend(page_prices)
        all_snapshots.extend(page_snapshots)
        log.info("  第 %d/%d 页: %d 条", page_no, total_pages, len(page_prices))

    last_page_html = filtered_html

    if not all_listing_prices:
        raise RuntimeError("面积结果页未抓到在售单价")

    quote_avg = mean(all_listing_prices)
    log.info("[6] 在售均价: %s (共 %d 条)", format_price(quote_avg), len(all_listing_prices))

    # ---- 6. 小区详情页 ----
    detail_url = parsers.find_detail_link(last_page_html) or detail_url
    xiaoqu_id = None
    if detail_url:
        m = re.search(r"/xiaoqu/(\d+)/", detail_url)
        xiaoqu_id = m.group(1) if m else None
    log.info("[7] 小区详情: xiaoqu_id=%s", xiaoqu_id)

    detail_clicked, detail_tab = await _click_detail_link(browser, page, detail_url)
    community_avg_price = None
    deal_prices: list[float] = []
    deal_records: list = []

    if detail_clicked and detail_tab is not None:
        await detail_tab
        await asyncio.sleep(3)
        detail_html = await detail_tab.get_content()
        await _dump(detail_tab, "ke_detail")

        community_avg_price = parsers.parse_community_avg_price(detail_html)
        deal_records = parsers.parse_deal_records(detail_html)
        deal_prices = parsers.filter_deal_prices_by_area(deal_records, area_min, area_max)
        log.info("[8] 小区均价=%s 成交记录=%d条(筛选后)", community_avg_price, len(deal_prices))
    else:
        log.warning("未能打开小区详情页，跳过成交记录采集")

    # ---- 7. 算法决策 ----
    effective_quote = community_avg_price or quote_avg
    deal_avg = mean(deal_prices) if deal_prices else None
    decision = decide(
        effective_quote,
        deal_avg,
        config.DEAL_DIFF_THRESHOLD,
        config.get_no_deal_discount(),
    )

    elapsed = round(time.time() - started_at, 2)

    return {
        "community_name": community_name,
        "area_min": area_min,
        "area_max": area_max,
        "listing_snapshots": all_snapshots,
        "quote_prices": all_listing_prices,
        "quote_avg": round_price(quote_avg),
        "community_avg_price": community_avg_price,
        "deal_prices": deal_prices,
        "deal_avg": round_price(deal_avg),
        "deal_records": deal_records,
        "final_price": round_price(decision.final_price),
        "branch": decision.branch,
        "listing_count": len(all_listing_prices),
        "deal_count": len(deal_prices),
        "elapsed_seconds": elapsed,
        # 调试跟踪
        "trace": {
            "home_blocked": False,
            "search_url": keyword_url,
            "area_url": filtered_url,
            "area_pages": total_pages,
            "detail_clicked": detail_clicked,
            "detail_url": detail_url,
        },
    }


# ============================================================
# 打印 & 主流程
# ============================================================

def _print_mvp(result_data: dict):
    """调用共用输出模块。"""
    print_mvp_result(
        platform="贝壳",
        community_name=result_data["community_name"],
        area_min=result_data["area_min"],
        area_max=result_data["area_max"],
        trace={
            "home_blocked": False,
            "search_url": result_data["trace"]["search_url"],
            "area_ok": True,
            "area_url": result_data["trace"]["area_url"],
            "area_pages": result_data["trace"]["area_pages"],
            "detail_ok": result_data["trace"]["detail_clicked"],
            "detail_url": result_data["trace"]["detail_url"],
        },
        listings={
            "count": result_data["listing_count"],
            "avg": result_data["quote_avg"],
            "snapshots": result_data["listing_snapshots"],
        },
        deals={
            "count": result_data["deal_count"],
            "avg": result_data["deal_avg"],
            "records": result_data.get("deal_records", []),
        },
        result={
            "quote_avg": result_data["quote_avg"],
            "deal_avg": result_data["deal_avg"],
            "final_price": result_data["final_price"],
            "branch": result_data["branch"],
        },
        elapsed=result_data["elapsed_seconds"],
    )


async def main(
    community_name: str = DEFAULT_COMMUNITY,
    area_min: float = DEFAULT_AREA_MIN,
    area_max: float = DEFAULT_AREA_MAX,
    manual_login: bool = True,
    debug: bool = False,
):
    if debug:
        set_debug_mode(True)

    log.info("启动 Edge 浏览器")
    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )

    try:
        print(
            f"\n贝壳 MVP 测试"
            f"\n小区: {community_name}"
            f"\n面积: {area_min:.0f} - {area_max:.0f} ㎡"
            f"\n"
        )

        result = await run_ke_collect(
            browser,
            community_name=community_name,
            area_min=area_min,
            area_max=area_max,
            manual_login=manual_login,
        )
        _print_mvp(result)

        await _wait_for_manual_close()
    finally:
        browser.stop()
        log.info("测试结束")


def cli():
    parser = argparse.ArgumentParser(description="贝壳 MVP 测试脚本")
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="启动后等待人工完成登录，回车后继续。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML 到 debug 目录。",
    )
    parser.add_argument(
        "--community",
        type=str,
        default=DEFAULT_COMMUNITY,
        help=f"小区名称，默认 {DEFAULT_COMMUNITY}",
    )
    parser.add_argument(
        "--min",
        type=float,
        default=DEFAULT_AREA_MIN,
        help=f"面积下限，默认 {DEFAULT_AREA_MIN}",
    )
    parser.add_argument(
        "--max",
        type=float,
        default=DEFAULT_AREA_MAX,
        help=f"面积上限，默认 {DEFAULT_AREA_MAX}",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(
        main(
            community_name=args.community,
            area_min=args.min,
            area_max=args.max,
            manual_login=args.manual_login,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    cli()
