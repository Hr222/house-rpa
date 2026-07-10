# -*- coding: utf-8 -*-
"""连贯流程验收脚本。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Optional

import nodriver as uc

import config
from app import parsers
from app.debug_utils import dump_html as shared_dump_html
from app.debug_utils import set_debug_mode
from app.ke_adapter import _click_by_candidates, _get_search_input, _human_click, _submit_search
from app.platforms.ke_constants import START_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("combined-flow-test")

COMMUNITY_NAME = "绿景虹湾"
AREA_SEGMENT = "a3"
AREA_LABEL = "70-90㎡"


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


async def click_area_segment(page) -> bool:
    selectors = [
        f"a[href='/ershoufang/{AREA_SEGMENT}/']",
        f"a[href$='/{AREA_SEGMENT}/']",
    ]
    for selector in selectors:
        try:
            element = await page.select(selector, timeout=2)
        except Exception:
            element = None
        if element and await _human_click(page, element, "area segment"):
            return True

    try:
        element = await page.find(AREA_LABEL, timeout=2)
    except Exception:
        element = None
    if element and await _human_click(page, element, "area segment"):
        return True

    return False


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


def print_summary(
    *,
    open_file: Optional[Path],
    login_file: Optional[Path],
    more_file: Optional[Path],
    area_file: Optional[Path],
    search_file: Optional[Path],
    error_file: Optional[Path],
    more_clicked: bool,
    area_clicked: bool,
    area_url: str,
    area_hit_segment: bool,
    area_login: bool,
    search_attempted: bool,
    search_url: str | None,
    search_login: bool,
    prices_count: int,
    detail_url: str | None,
    conclusion: str,
):
    print()
    print("=" * 60)
    print("连贯流程测试完成")
    print(f"打开首页 HTML: {open_file}")
    print(f"登录后 HTML: {login_file}")
    print(f"更多选项后 HTML: {more_file}")
    print(f"面积点击后 HTML: {area_file}")
    print(f"搜索后 HTML: {search_file}")
    print(f"异常现场 HTML: {error_file}")
    print(f"更多选项点击: {more_clicked}")
    print(f"面积 70-90㎡ 点击: {area_clicked}")
    print(f"面积步骤 URL: {area_url}")
    print(f"面积步骤命中 a3: {area_hit_segment}")
    print(f"面积步骤直接跳登录: {area_login}")
    print(f"是否继续执行搜索: {search_attempted}")
    print(f"搜索后 URL: {search_url}")
    print(f"搜索后跳登录: {search_login}")
    print(f"结果页在售单价数: {prices_count}")
    print(f"结果页详情链接: {detail_url}")
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
    login_file = None
    more_file = None
    area_file = None
    search_file = None

    try:
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        open_file = await dump_html(page, "combined_opened")

        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(START_URL)
            await page
            await asyncio.sleep(3)
            login_file = await dump_html(page, "combined_after_manual_login")

        more_clicked = await _click_by_candidates(
            page,
            "more options",
            selectors=[".more.btn-more", ".btn-more", ".more"],
            texts=["更多选项", "更多条件", "更多"],
        )
        log.info("more options clicked: %s", more_clicked)
        await asyncio.sleep(1.5)
        more_file = await dump_html(page, "combined_after_more")

        area_clicked = await click_area_segment(page)
        if not area_clicked:
            raise RuntimeError("未能点击建筑面积 70-90㎡")
        await asyncio.sleep(3)

        area_url = page.target.url or ""
        area_html = await page.get_content()
        area_file = await dump_html(page, "combined_after_area")
        area_hit_segment = f"/ershoufang/{AREA_SEGMENT}/" in area_url
        area_login = is_login_url(area_url) or is_login_html(area_html)

        if area_login:
            print_summary(
                open_file=open_file,
                login_file=login_file,
                more_file=more_file,
                area_file=area_file,
                search_file=None,
                error_file=None,
                more_clicked=more_clicked,
                area_clicked=area_clicked,
                area_url=area_url,
                area_hit_segment=area_hit_segment,
                area_login=area_login,
                search_attempted=False,
                search_url=None,
                search_login=False,
                prices_count=0,
                detail_url=None,
                conclusion="流程成功：更多选项和 70-90㎡ 已执行，站点在面积步骤直接拦到登录页。",
            )
            await wait_for_manual_close()
            return

        inp = await _get_search_input(page)
        clicked = await _human_click(page, inp, "search input")
        if not clicked:
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

        search_url = page.target.url or ""
        search_html = await page.get_content()
        search_file = await dump_html(page, "combined_after_search")
        search_login = is_login_url(search_url) or is_login_html(search_html)
        prices = parsers.parse_listing_prices(search_html) if not search_login else []
        detail_url = None if search_login else parsers.find_detail_link(search_html)

        if search_login:
            conclusion = "流程成功：更多选项、70-90㎡、绿景虹湾搜索均已执行，搜索后跳到登录页。"
        elif prices or detail_url:
            conclusion = "流程成功：更多选项、70-90㎡、绿景虹湾搜索均已执行，并进入结果页。"
        else:
            conclusion = "流程未完全达到预期：搜索已执行，但未识别到登录页或结果页，请查看调试 HTML。"

        print_summary(
            open_file=open_file,
            login_file=login_file,
            more_file=more_file,
            area_file=area_file,
            search_file=search_file,
            error_file=None,
            more_clicked=more_clicked,
            area_clicked=area_clicked,
            area_url=area_url,
            area_hit_segment=area_hit_segment,
            area_login=area_login,
            search_attempted=True,
            search_url=search_url,
            search_login=search_login,
            prices_count=len(prices),
            detail_url=detail_url,
            conclusion=conclusion,
        )

        await wait_for_manual_close()
    except Exception:
        error_file = None
        if page is not None:
            error_file = await dump_html(page, "combined_error")
        if open_file and more_file and area_file:
            print_summary(
                open_file=open_file,
                login_file=login_file,
                more_file=more_file,
                area_file=area_file,
                search_file=search_file,
                error_file=error_file,
                more_clicked=True,
                area_clicked=True,
                area_url=page.target.url or "",
                area_hit_segment=False,
                area_login=False,
                search_attempted=search_file is not None,
                search_url=page.target.url or "",
                search_login=False,
                prices_count=0,
                detail_url=None,
                conclusion="流程异常中断，请查看异常现场 HTML。",
            )
        raise
    finally:
        browser.stop()


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="先人工登录，回车后再继续执行连贯流程测试。",
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
