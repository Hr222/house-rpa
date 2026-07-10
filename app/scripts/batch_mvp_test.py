# -*- coding: utf-8 -*-
"""批量场景演示脚本。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass

import nodriver as uc

import config
from app.debug_utils import set_debug_mode
from app.logging_utils import setup_logging
from app.models import InquiryRequest
from app.price_utils import format_price
from app.registry import build_default_adapters
from app.service import RPAInquiryService
from app.window_control import ensure_browser_foreground

log = logging.getLogger("batch-demo")

DEFAULT_INTERVAL_SECONDS = 30


@dataclass(slots=True)
class DemoScenario:
    community_name: str
    area_min: float
    area_max: float


DEFAULT_SCENARIOS = [
    DemoScenario("绿景虹湾", 70.0, 90.0),
    DemoScenario("半岛城邦花园一期", 110.0, 140.0),
    DemoScenario("皇岗花园大厦", 70.0, 90.0),
]


async def confirm_ready(service: RPAInquiryService, adapters):
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


def parse_scenario(value: str) -> DemoScenario:
    parts = [item.strip() for item in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("场景格式必须为: 小区名,最小面积,最大面积")

    community_name = parts[0]
    if not community_name:
        raise argparse.ArgumentTypeError("小区名不能为空")

    try:
        area_min = float(parts[1])
        area_max = float(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("面积必须是数字") from exc

    if area_min <= 0 or area_max <= 0 or area_min >= area_max:
        raise argparse.ArgumentTypeError("面积范围不合法，要求 0 < areaMin < areaMax")

    return DemoScenario(community_name, area_min, area_max)


def build_scenarios(custom_scenarios: list[DemoScenario] | None) -> list[DemoScenario]:
    return custom_scenarios or list(DEFAULT_SCENARIOS)


async def run_demo_inquiry(service: RPAInquiryService, scenario: DemoScenario, index: int):
    request = InquiryRequest(
        community_name=scenario.community_name,
        area_min=scenario.area_min,
        area_max=scenario.area_max,
        request_id=f"demo-batch-{index:03d}",
    )
    print("\n" + "=" * 60)
    print(
        f"模拟接单[{index}]: 查询小区={request.community_name}, "
        f"筛选面积={request.area_min:.0f}至{request.area_max:.0f}"
    )
    print("=" * 60)

    result = await service.run_inquiry(request)

    print_platform_details(result)
    print(f"\n在售均价(单位:元/平): {format_price(result.quote_avg)}")
    print(f"成交均价(单位:元/平): {format_price(result.deal_avg)}")
    print(f"最终取值(单位:元/平): {format_price(result.final_price)}")
    print_return_body(result)
    print("=" * 60)
    return result


def print_scenario_plan(scenarios: list[DemoScenario]):
    print("\n本次演示场景:")
    for index, scenario in enumerate(scenarios, start=1):
        print(
            f"{index}. 小区={scenario.community_name} "
            f"面积={scenario.area_min:.0f}-{scenario.area_max:.0f}"
        )


def print_final_summary(summary_rows: list[tuple[DemoScenario, object]]):
    print("\n批量演示汇总:")
    for index, (scenario, result) in enumerate(summary_rows, start=1):
        if isinstance(result, Exception):
            print(
                f"{index}. {scenario.community_name} "
                f"{scenario.area_min:.0f}-{scenario.area_max:.0f} -> 异常: {result}"
            )
            continue

        print(
            f"{index}. {scenario.community_name} "
            f"{scenario.area_min:.0f}-{scenario.area_max:.0f} -> "
            f"quoteAvg={format_price(result.quote_avg)}, "
            f"dealAvg={format_price(result.deal_avg)}, "
            f"finalPrice={format_price(result.final_price)}"
        )


async def main(
    *,
    debug: bool = False,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    custom_scenarios: list[DemoScenario] | None = None,
):
    if debug:
        set_debug_mode(True)

    setup_logging()
    log.info("[1] 启动 Edge 批量演示浏览器")
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

    scenarios = build_scenarios(custom_scenarios)
    summary_rows: list[tuple[DemoScenario, object]] = []

    try:
        await confirm_ready(service, adapters)
        print_scenario_plan(scenarios)

        for index, scenario in enumerate(scenarios, start=1):
            try:
                result = await run_demo_inquiry(service, scenario, index)
                summary_rows.append((scenario, result))
            except Exception as exc:
                log.exception("场景执行异常: %s", scenario)
                summary_rows.append((scenario, exc))

            if index < len(scenarios):
                print(f"\n等待 {interval_seconds}s，模拟下一次接单...")
                await asyncio.sleep(interval_seconds)

        print_final_summary(summary_rows)
    finally:
        browser.stop()
        log.info("批量演示结束")


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML 到 debug 目录。",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="上一场景结束到下一场景开始前的等待秒数，默认 30。",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        type=parse_scenario,
        help="自定义演示场景，格式: 小区名,最小面积,最大面积。可重复传入多次。",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(
        main(
            debug=args.debug,
            interval_seconds=args.interval_seconds,
            custom_scenarios=args.scenario,
        )
    )


if __name__ == "__main__":
    cli()
