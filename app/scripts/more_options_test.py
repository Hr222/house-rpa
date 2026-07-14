# -*- coding: utf-8 -*-
"""最小化测试：打开贝壳二手房页并点击“更多选项”。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Iterable, Optional

import nodriver as uc

from app.core import config
from app.utils.debug_utils import dump_html as shared_dump_html
from app.utils.debug_utils import set_debug_mode
from app.platforms.ke_constants import START_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("more-options-test")


async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


async def is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def first_candidate(page, selectors: Iterable[str]):
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


async def element_text(element) -> str:
    try:
        text = await element.apply("(el) => (el.innerText || el.textContent || '').trim()")
        return text or ""
    except Exception:
        return ""


async def click_element(page, element) -> str:
    try:
        await element.scroll_into_view()
    except Exception:
        pass

    try:
        await element.mouse_move()
    except Exception:
        pass

    try:
        await element.mouse_click()
        await page
        await asyncio.sleep(1.0)
        return "mouse_click"
    except Exception as mouse_error:
        log.info("mouse_click failed: %s", mouse_error)

    await element.click()
    await page
    await asyncio.sleep(1.0)
    return "element.click"


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

        before_file = await dump_html(page, "more_options_before")

        selectors = [
            ".more.btn-more",
            ".btn-more",
            ".more",
            "[class*='more'][class*='btn']",
        ]
        selector, element = await first_candidate(page, selectors)
        if not element:
            try:
                element = await page.find("更多选项", timeout=2)
                selector = "text:更多选项"
            except Exception:
                element = None

        if not element:
            raise RuntimeError("未找到“更多选项”元素")

        text = await element_text(element)
        log.info("candidate found via %s, text=%r", selector, text)

        click_mode = await click_element(page, element)
        log.info("clicked via %s", click_mode)

        after_file = await dump_html(page, "more_options_after")

        before_html = before_file.read_text(encoding="utf-8", errors="ignore") if before_file else ""
        after_html = after_file.read_text(encoding="utf-8", errors="ignore") if after_file else ""
        changed = before_html != after_html if before_file and after_file else None
        log.info("html changed after click: %s", changed)
        log.info("current url: %s", page.target.url)

        print()
        print("=" * 60)
        print("更多选项测试完成")
        print(f"命中方式: {selector}")
        print(f"点击方式: {click_mode}")
        print(f"页面 HTML 是否变化: {changed}")
        print(f"点击前 HTML: {before_file}")
        print(f"点击后 HTML: {after_file}")
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
