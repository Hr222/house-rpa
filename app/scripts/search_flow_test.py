# -*- coding: utf-8 -*-
"""最小化测试：固定场景搜索贝壳二手房。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import nodriver as uc

import config
from app import parsers
from app.debug_utils import dump_html as shared_dump_html
from app.debug_utils import set_debug_mode
from app.ke_adapter import _get_search_input, _human_click, _pick_segments, _submit_search
from app.platforms.ke_constants import START_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("search-flow-test")

COMMUNITY_NAME = "绿景虹湾"
AREA_MIN = 70.0
AREA_MAX = 90.0


async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url


def is_login_html(html: str) -> bool:
    markers = (
        'meta name="ke-passport" content="LOGIN"',
        'id="login"',
        "请输入手机号",
        "请输入密码",
        "手机快捷登录",
    )
    return any(marker in html for marker in markers)


async def main(debug: bool = False):
    if debug:
        set_debug_mode(True)

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    try:
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)

        await dump_html(page, "search_flow_before")

        segments = _pick_segments(AREA_MIN, AREA_MAX)
        log.info("segments matched for %.0f-%.0f: %s", AREA_MIN, AREA_MAX, segments)

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

        current_url = page.target.url or ""
        html = await page.get_content()
        after_file = await dump_html(page, "search_flow_after_search")

        if is_login_url(current_url) or is_login_html(html):
            print()
            print("=" * 60)
            print("搜索测试完成")
            print("结果: 已跳转登录页，按约定视为成功")
            print(f"当前 URL: {current_url}")
            print(f"点击后 HTML: {after_file}")
            print("=" * 60)
            print()
            await asyncio.sleep(5)
            return

        prices = parsers.parse_listing_prices(html)
        detail_url = parsers.find_detail_link(html)
        log.info("result page parsed: prices=%d detail_url=%s", len(prices), detail_url)

        xiaoqu_id = None
        if detail_url:
            match = re.search(r"/xiaoqu/(\d+)", detail_url)
            if match:
                xiaoqu_id = match.group(1)

        area_url = None
        area_file = None
        if xiaoqu_id and segments:
            area_url = f"https://sz.ke.com/ershoufang/{segments[0]}c{xiaoqu_id}/"
            log.info("navigating to area url: %s", area_url)
            page = await page.get(area_url)
            await page
            await asyncio.sleep(3)
            area_file = await dump_html(page, "search_flow_area_segment")

        print()
        print("=" * 60)
        print("搜索测试完成")
        print("结果: 未跳登录页，但搜索已执行")
        print(f"当前 URL: {current_url}")
        print(f"在售结果数: {len(prices)}")
        print(f"详情链接: {detail_url}")
        print(f"面积档位: {segments}")
        print(f"面积过滤 URL: {area_url}")
        print(f"搜索后 HTML: {after_file}")
        print(f"面积页 HTML: {area_file}")
        print("=" * 60)
        print()

        await asyncio.sleep(5)
    finally:
        browser.stop()


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML 到 debug 目录。",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(main(debug=args.debug))


if __name__ == "__main__":
    cli()
