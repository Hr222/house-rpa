# -*- coding: utf-8 -*-
"""RPA 演示脚本。"""

from __future__ import annotations

import argparse
import asyncio
import logging

import nodriver as uc

from app.core import config
from app.utils.debug_utils import set_debug_mode
from app.utils.logging_utils import setup_logging
from app.core.models import InquiryRequest
from app.core.price_utils import format_price
from app.registry import build_default_adapters
from app.service import RPAInquiryService
from app.utils.window_control import ensure_browser_foreground

TEST_COMMUNITY = "绿景虹湾"
TEST_AREA_MIN = 70.0
TEST_AREA_MAX = 90.0

log = logging.getLogger("demo")


async def confirm_ready(service: RPAInquiryService, adapters):
    """人工登录后，回车触发平台 ready 检查。"""
    while True:
        lines = ["", "浏览器已打开，请先在前台完成人工登录。"]
        for adapter in adapters:
            session = service.sessions[adapter.code]
            lines.append(f"- {session.name}: {session.start_url}")
        lines.append("登录完成后回到终端按回车确认...")
        await asyncio.to_thread(input, "\n".join(lines))

        all_ready = True
        for adapter in adapters:
            session = service.sessions[adapter.code]
            ready, message = await adapter.check_ready(session)
            if ready:
                log.info("%s 已就绪", session.name)
            else:
                all_ready = False
                log.warning("%s 尚未就绪: %s", session.name, message)
                process = getattr(service.browser, "_process", None)
                if process and getattr(process, "pid", None):
                    ensure_browser_foreground(process.pid)

        if all_ready:
            return


def print_platform_details(result):
    for platform_result in result.platform_results:
        print(f"\n{platform_result.name} 平台结果")
        print(f"状态: {platform_result.status}")
        if platform_result.reason:
            print(f"原因: {platform_result.reason}")

        if platform_result.listing_snapshots:
            for item in platform_result.listing_snapshots:
                print(
                    f"{platform_result.name}: "
                    f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
                    f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
                    f"总价: {item.total_price or ''}万}}"
                )
        else:
            print(f"{platform_result.name}: 未抓到房源摘要")


def print_return_body(result):
    print("\n模拟返回 body")
    print(
        "{"
        f'"quoteAvg": {format_price(result.quote_avg)}, '
        f'"dealAvg": {format_price(result.deal_avg)}, '
        f'"finalPrice": {format_price(result.final_price)}'
        "}"
    )


async def run_demo_inquiry(service: RPAInquiryService):
    request = InquiryRequest(
        community_name=TEST_COMMUNITY,
        area_min=TEST_AREA_MIN,
        area_max=TEST_AREA_MAX,
    )
    print("\n" + "=" * 60)
    print(f"模拟接单: 查询小区={request.community_name}, 筛选面积={request.area_min:.0f}至{request.area_max:.0f}")
    print("=" * 60)

    result = await service.run_inquiry(request)

    print_platform_details(result)
    print(f"\n在售均价(单位:元/平): {format_price(result.quote_avg)}")
    print(f"成交均价(单位:元/平): {format_price(result.deal_avg)}")
    print(f"最终取值(单位:元/平): {format_price(result.final_price)}")
    print_return_body(result)
    print("=" * 60)


async def main(debug: bool = False):
    if debug:
        set_debug_mode(True)

    setup_logging()
    log.info("[1] 启动 Edge 演示浏览器")
    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )

    adapters = build_default_adapters()
    service = RPAInquiryService(browser, adapters)
    await service.start()

    pid = getattr(getattr(browser, "_process", None), "pid", None)
    if pid:
        ensure_browser_foreground(pid)

    try:
        await confirm_ready(service, adapters)

        print(
            f"\n默认演示请求: {TEST_COMMUNITY} {TEST_AREA_MIN:.0f}-{TEST_AREA_MAX:.0f}㎡"
            "\n直接回车重复演示，输入 quit 退出。"
        )

        while True:
            await run_demo_inquiry(service)
            command = (await asyncio.to_thread(input, "\n> ")).strip().lower()
            if command == "quit":
                break
    finally:
        browser.stop()
        log.info("演示结束")


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
