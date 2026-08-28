# -*- coding: utf-8 -*-
"""房天下小区挂牌页 URL 初始化 MVP。

只验证单个小区的挂牌页入口：搜索结果经结构化房源小区名确认后，读取小区名链接
`/house-xm{id}/`，实打开后再次校验并输出可记录的挂牌页 URL。

用法：
  python -m scripts.rpa.fang_community_page_mvp --manual-login --community "翰熙典居"
"""

from __future__ import annotations

import argparse
import asyncio
from html import unescape
import logging
from pathlib import Path
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import nodriver as uc

from app.rpa.core import config
from app.rpa.parsers import fang as fang_parsers
from app.rpa.platforms.base import community_name_match, has_matching_community_snapshots
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging


setup_logging()
log = logging.getLogger(__name__)


async def dump_html(page, name: str) -> Optional[Path]:
    """在开启 --debug 时导出当前 HTML。"""
    return await shared_dump_html(page, name, logger=log)


def _blocked_reason(url: str, html: str, expected_host: str) -> Optional[str]:
    """识别 MVP 阶段已知的登录、验证码和城市漂移现场。"""
    url_lower = (url or "").lower()
    if any(marker in url_lower for marker in ("captcha", "verifycode", "antibot", "antispam")):
        return "命中验证码拦截 URL"

    if any(marker in (html or "") for marker in ("请输入验证码", "验证后继续访问", "请完成验证", "滑动验证")):
        return "命中验证码拦截页面"

    if any(marker in (html or "") for marker in ("请输入手机号", "请输入密码", "手机快捷登录", "扫码登录")):
        return "命中登录页面"

    actual_host = (urlparse(url or "").hostname or "").lower()
    if actual_host != expected_host:
        return f"城市漂移：期望域名 {expected_host}，实际 {actual_host or '空'}"
    return None


async def wait_for_manual_resolution(label: str, reason: str) -> None:
    """保留浏览器现场，等待人工完成登录或验证码。"""
    prompt = (
        f"\n{label}{reason}。请在浏览器完成处理后，"
        "回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


async def ensure_accessible(page, *, label: str, expected_host: str) -> str:
    """保留人工处理后的当前页，直到页面可继续解析。"""
    attempt = 1
    while True:
        await page
        html = await page.get_content()
        url = page.target.url or ""
        reason = _blocked_reason(url, html, expected_host)
        if reason is None:
            return html

        log.warning("%s不可用（第 %d 次）：%s，URL=%s", label, attempt, reason, url)
        await wait_for_manual_resolution(label, reason)
        await page
        await asyncio.sleep(2)
        attempt += 1


async def is_interactable(element) -> bool:
    """避免优先命中隐藏的搜索控件。"""
    try:
        position = await element.get_position()
        return bool(position and position.width > 0 and position.height > 0)
    except Exception:
        return False


async def get_search_input(page):
    """定位房天下二手房搜索框。"""
    selectors = (
        "input#input_keyw1",
        "input[name='input_keyw1']",
        "input[placeholder*='小区']",
    )
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
    """定位房天下二手房搜索提交按钮。"""
    selectors = (
        "input#kesfqbfylb_A01_11_02",
        "input[type='submit'].btn",
        "input[type='submit'][value*='搜']",
    )
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
    """以既有 MVP 的鼠标优先、JS 回退方式点击页面元素。"""
    if element is None:
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
            await asyncio.sleep(1)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s点击失败：%s", label, last_error)
    return False


async def search_community(page, community_name: str) -> None:
    """在城市二手房首页提交目标小区搜索。"""
    selector, search_input = await get_search_input(page)
    if search_input is None:
        raise RuntimeError("未找到房天下搜索框")
    log.info("搜索框命中：%s", selector)

    if not await human_click(page, search_input, "搜索框"):
        raise RuntimeError("搜索框点击失败")
    try:
        await search_input.clear_input()
    except Exception:
        pass
    await search_input.send_keys(community_name)
    await page
    await asyncio.sleep(1)

    submit_selector, submit_button = await get_search_submit(page)
    if submit_button is not None and await human_click(page, submit_button, "搜索按钮"):
        log.info("通过 %s 提交小区搜索", submit_selector)
        await asyncio.sleep(3)
        return

    try:
        await search_input.send_keys("\r")
        await page
        await asyncio.sleep(3)
        log.info("通过回车提交小区搜索")
        return
    except Exception as exc:
        raise RuntimeError("未能提交房天下小区搜索") from exc


def find_matching_listing_url(
    result_html: str,
    community_name: str,
    result_url: str,
) -> Optional[str]:
    """从搜索结果 HTML 读取名称匹配小区的 `/house-xm{id}/` 地址。"""
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        result_html or "",
        re.S | re.I,
    ):
        attrs = match.group("attrs")
        href_match = re.search(
            r"\bhref=[\"'](?P<href>[^\"']*/house-xm[^\"']*)[\"']",
            attrs,
            re.I,
        )
        if href_match is None:
            continue
        title_match = re.search(r"\btitle=[\"'](?P<title>[^\"']*)[\"']", attrs, re.I)
        title = unescape(title_match.group("title")) if title_match else ""
        text = unescape(re.sub(r"<[^>]+>", "", match.group("body"))).strip()
        if community_name_match(community_name, title) or community_name_match(
            community_name,
            text,
        ):
            listing_url = urljoin(result_url, href_match.group("href"))
            if "/house-xm" in urlparse(listing_url).path:
                return listing_url
    return None


async def wait_for_manual_close() -> None:
    """在结束前保留浏览器，方便核验实际页面。"""
    await asyncio.to_thread(input, "\n浏览器保持打开。核对完成后按回车结束脚本...\n")


async def main(city: str, community_name: str, manual_login: bool, debug: bool) -> None:
    """执行一次房天下小区挂牌页 URL 初始化。"""
    if debug:
        set_debug_mode(True)

    start_url = get_start_url("fang", city)
    expected_host = (urlparse(start_url).hostname or "").lower()
    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    page = None
    try:
        page = await browser.get(start_url)
        await page
        await asyncio.sleep(3)

        if manual_login:
            await wait_for_manual_resolution("首页", "需要人工确认登录状态")
            await page.get(start_url)
            await page
            await asyncio.sleep(2)
        await ensure_accessible(
            page,
            label="首页",
            expected_host=expected_host,
        )
        await dump_html(page, "fang_community_page_home")

        await search_community(page, community_name)
        result_html = await ensure_accessible(
            page,
            label="搜索结果页",
            expected_host=expected_host,
        )
        await dump_html(page, "fang_community_page_search")

        snapshots = fang_parsers.parse_listing_snapshots(result_html)
        if not has_matching_community_snapshots(snapshots, community_name):
            examples = sorted({item.community_name for item in snapshots if item.community_name})[:5]
            raise RuntimeError(
                f"搜索结果未在结构化房源中匹配目标小区：{community_name}；"
                f"页面小区示例：{examples}"
            )

        result_url = page.target.url or start_url
        listing_page_url = find_matching_listing_url(
            result_html,
            community_name,
            result_url,
        )
        if listing_page_url is None:
            raise RuntimeError(f"未找到目标小区的 /house-xm 挂牌页链接：{community_name}")

        await page.get(listing_page_url)
        await page
        await asyncio.sleep(2)
        listing_html = await ensure_accessible(
            page,
            label="小区挂牌页",
            expected_host=expected_host,
        )
        await dump_html(page, "fang_community_page_listing")
        listing_snapshots = fang_parsers.parse_listing_snapshots(listing_html)
        if not has_matching_community_snapshots(listing_snapshots, community_name):
            raise RuntimeError(f"挂牌页未匹配目标小区：{community_name}")

        actual_listing_url = page.target.url or listing_page_url
        if "/house-xm" not in urlparse(actual_listing_url).path:
            raise RuntimeError(f"挂牌页跳转到非小区 URL：{actual_listing_url}")
        print(f"挂牌页 URL: {actual_listing_url}")
        log.info(
            "目标小区挂牌页已确认：%s（结构化房源 %d 条）",
            community_name,
            len(listing_snapshots),
        )

        await wait_for_manual_close()
    except Exception:
        if page is not None:
            await dump_html(page, "fang_community_page_error")
        log.exception("房天下小区页面 URL 发现失败")
        await wait_for_manual_close()
        raise
    finally:
        browser.stop()


def cli() -> None:
    """解析命令行参数并启动 nodriver 协程。"""
    parser = argparse.ArgumentParser(description="房天下小区挂牌页 URL 初始化 MVP")
    parser.add_argument("--city", default="深圳", help="城市名称，默认：深圳")
    parser.add_argument("--community", default="翰熙典居", help="目标小区名称")
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="打开首页后先暂停，供人工登录或确认验证码状态。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="导出首页、搜索结果和挂牌页 HTML 到 debug 目录。",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(
        main(
            city=args.city,
            community_name=args.community,
            manual_login=args.manual_login,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    cli()
