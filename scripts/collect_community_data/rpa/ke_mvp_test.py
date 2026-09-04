# -*- coding: utf-8 -*-
"""贝壳 MVP 测试脚本（URL 直达抓取形式）。

抓取形式（2026-09-03 整改，对齐 ajk/lyj 模板）：小区挂牌入口已由统一
初始化工具存入 community_platform_pages（/ershoufang/c{ID}），本脚本按
community_id 直达挂牌列表页，不走"首页 → 搜索 → 面积筛选"的模拟
点击链路；读 HTML 解析在售快照（工程 parser）。

流程：读取库内 ke 入口 → 首页先行建立会话（--manual-login 时人工
过验证码/登录）→ 采集主流程委托工程 collector.collect_listing_by_url
（与工程 shell 同一函数：风控协议、空态识别、归属校验、分页 URL
直达翻页全部单源）；MVP 只做 PlatformResult → CommunityCollection
映射与结果打印。--area 面积过滤改用工程 selection（面积口径已剥离
出 RPA，见 base.py 说明）。

口径：贝壳只采在售房源，不采成交、不采小区均价（deals 与
community_avg_price 恒空）。

用法：
  python -m scripts.collect_community_data.rpa.ke_mvp_test --community-id 170
  python -m scripts.collect_community_data.rpa.ke_mvp_test --listing-url "https://sz.ke.com/ershoufang/c2411049784546" --debug
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import nodriver as uc
from nodriver.core import util as nodriver_util

from app.algorithm.selection import select_listings_for_estimation
from app.rpa.core import config
from app.rpa.core.status import PlatformResultStatus
from app.rpa.platforms import KePlatformAdapter
from app.rpa.platforms.base import wait_and_reload_after_block
from app.rpa.platforms.ke import collector as ke_adapter
from app.rpa.platforms.ke.constants import START_URL
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging
from scripts.collect_community_data.models import CommunityCollection


setup_logging()
log = logging.getLogger("ke-mvp-test")
_platform = KePlatformAdapter()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PAGES_DB = PROJECT_ROOT / "persist" / "property_records.sqlite3"

# 翻页防失控兜底：单小区最大翻页数
MAX_PAGES = 50

# 常驻浏览器引用：批次结束保持存活（atexit 摘除，脚本退出不关闭）；
# 终端 Ctrl+C 终止时由 terminate_browser() 同步关闭——事件循环被冻结，
# MVP 协程的 finally 无法自行收尾，必须在进程退出层处理
_browser = None


async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def print_listing_snapshots(snapshots: list):
    if not snapshots:
        print("贝壳: 未抓到房源摘要")
        return
    for item in snapshots:
        print(
            "贝壳: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )
        _url = item.listing_url or "-"
        print("  房源链接:", _url if len(_url) <= 72 else _url[:69] + "...")


async def browser_gone(tab) -> bool:
    """人工关闭浏览器（窗口/进程）后连接断开，探测返回 True。"""
    try:
        await tab.get_content()
        return False
    except Exception:
        return True


async def wait_for_manual_login():
    """风控交互：暂停等人工在浏览器处理，回车确认后继续。"""
    prompt = (
        "\n请在打开的浏览器里手动完成验证码 / 登录。"
        "\n完成后回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


# ============================================================
# 库内入口查找
# ============================================================

def resolve_listing_urls(community_ids: list[int]) -> list[dict]:
    """从房源记录库读取贝壳已初始化的挂牌入口。

    白名单语义：community_platform_pages 里没有 ke 入口的小区直接跳过。
    """
    con = sqlite3.connect(f"file:{PAGES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    targets: list[dict] = []
    for community_id in community_ids:
        row = con.execute(
            "SELECT community_id, source_community_name, listing_page_url "
            "FROM community_platform_pages WHERE community_id = ? AND source_platform = 'ke'",
            (community_id,),
        ).fetchone()
        if row is None or not row["listing_page_url"]:
            log.warning("[跳过] 小区 %s 无贝壳挂牌入口（未初始化）", community_id)
            continue
        targets.append(
            {
                "community_id": community_id,
                "community_name": row["source_community_name"],
                "listing_page_url": row["listing_page_url"],
            }
        )
    con.close()
    return targets


def terminate_browser() -> None:
    """同步关闭常驻浏览器进程（终端 Ctrl+C 终止时调用，不依赖事件循环）。"""
    global _browser
    browser, _browser = _browser, None
    if browser is None:
        return
    try:
        process = getattr(browser, "process", None)
        if process is not None:
            process.terminate()
    except Exception:
        log.debug("浏览器进程终止失败（可能已退出）")
    try:
        browser.stop()
    except Exception:
        pass


# ============================================================
# 单小区直达采集
# ============================================================

async def collect_community(
    tab,
    *,
    target: dict,
    area: Optional[float],
) -> CommunityCollection:
    """单小区直达采集：委托工程 collector.collect_listing_by_url（与工程
    shell 同一函数），风控协议、空态识别、归属校验、分页翻页全部单源；
    MVP 只做 PlatformResult → CommunityCollection 映射与日志打印。

    --area 时用工程 selection 做展示过滤（面积口径已剥离出 RPA）。
    贝壳不采成交、不采小区均价（deals 与 community_avg_price 恒空）。
    """
    result = await ke_adapter.collect_listing_by_url(
        page=tab,
        community_name=target["community_name"],
        listing_page_url=target["listing_page_url"],
        max_pages=MAX_PAGES,
    )

    listings = list(result.listing_snapshots)
    if area is not None and listings:
        selection = select_listings_for_estimation(listings, area)
        listings = list(selection.snapshots)
        log.info(
            "[面积筛选] 请求 %s㎡：工程 selection 保留 %d/%d 条（弱参考=%s）",
            area,
            len(listings),
            len(result.listing_snapshots),
            selection.uses_weak_reference,
        )

    for snapshot in listings:
        log.info(
            "[在售] %s | %s | %s㎡ | 总价 %s万 | 单价 %s元/㎡",
            snapshot.community_name or "-",
            snapshot.layout or "-",
            snapshot.area or "-",
            snapshot.total_price or "-",
            snapshot.unit_price or "-",
        )
    log.info(
        "[采集汇总] %s：在售 %d 条（贝壳不采成交与均价）",
        target["community_name"],
        len(listings),
    )

    blocked_reason = (
        result.reason
        if result.status in (
            PlatformResultStatus.WAIT_MANUAL_VERIFY,
            PlatformResultStatus.LOGIN_EXPIRED,
            PlatformResultStatus.ERROR,
        )
        else None
    )
    return CommunityCollection(
        platform="ke",
        community_id=target["community_id"],
        community_name=target["community_name"],
        listing_page_url=target["listing_page_url"],
        deal_page_url=None,  # 贝壳不采成交
        status=result.status,
        blocked_reason=blocked_reason,
        listings=listings,
        deals=[],  # 贝壳不采成交记录
        community_avg_price=result.community_avg_price,  # 贝壳不采小区均价
    )


async def main(
    community_ids: list[int],
    listing_url: Optional[str],
    area: Optional[float],
    manual_login: bool,
    debug: bool,
) -> list[CommunityCollection]:
    """常驻采集进程：一个浏览器会话逐小区抓取在售。

    返回逐小区原始 CommunityCollection（一个对象，两个 list；
    筛选计算在编排层）。首批清单采集完后进程不退出：模拟工程
    runtime 常驻——等待编排层下一个任务（控制台输入小区 ID 模拟
    IPC）；人工关闭浏览器后采集进程结束。
    """
    if debug:
        set_debug_mode(True)

    targets: list[dict] = []
    if listing_url:
        targets.append(
            {"community_id": None, "community_name": "直传URL", "listing_page_url": listing_url}
        )
    targets.extend(resolve_listing_urls(community_ids))
    if not targets:
        print("[错误] 没有可采集目标：--community-id 未命中贝壳入口，且未传 --listing-url")
        return
    log.info(
        "采集目标 %d 个：%s",
        len(targets),
        [t["community_name"] for t in targets],
    )

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    # 浏览器常驻：把实例从 nodriver 的 atexit 清理注册表移除，
    # 批次结束/脚本退出都不关闭浏览器（对齐工程 runtime 的常驻模式）
    nodriver_util.__registered__instances__.discard(browser)

    # 先开贝壳首页建立会话：指令必带 manual_login，首页先人工过
    # 验证码/登录，回车确认；再由工程风控件复检，干净后逐个直达小区
    tab = await browser.get(START_URL)
    await tab
    await asyncio.sleep(3)
    if manual_login:
        await wait_for_manual_login()
    await wait_and_reload_after_block(tab, _platform.detect_block, "贝壳首页")

    summaries: list[CommunityCollection] = []
    try:
        for target in targets:
            item = await collect_community(
                tab,
                target=target,
                area=area,
            )
            summaries.append(item)
            print_listing_snapshots(item.listings)

        # 采集完成：浏览器保持打开，等人工回车确认结束
        # （回车 / Ctrl+C 均会关闭浏览器窗口；期间人工关闭浏览器也会直接结束）。
        # 必须用单个 daemon 线程等回车：wait_for+to_thread 每次超时会留下
        # 堵在 stdin 的孤儿线程——回车被孤儿吃掉、Ctrl+C 后进程也因非
        # daemon 线程无法退出（2026-09-03 实测教训），这种写法不可用
        log.info("采集完成，浏览器保持打开")
        enter_pressed = threading.Event()

        def _wait_enter():
            try:
                input("\n按回车结束脚本并关闭浏览器窗口（Ctrl+C 同效）...\n")
            except EOFError:
                pass
            enter_pressed.set()

        threading.Thread(target=_wait_enter, daemon=True).start()
        while not enter_pressed.is_set():
            if await browser_gone(tab):
                log.info("检测到浏览器已关闭，脚本结束")
                break
            await asyncio.sleep(0.5)
    except Exception:
        if tab is not None:
            await dump_html(tab, "ke_error")
        log.exception("测试异常中断")
        raise
    finally:
        # 结束即关闭浏览器窗口：回车确认 / Ctrl+C / 出错 均覆盖。
        # Ctrl+C 终止时事件循环可能已停，先同步终止浏览器进程
        # （不依赖事件循环），再走 stop 收尾连接与临时目录。
        process = getattr(browser, "process", None)
        if process is not None:
            process.terminate()
        browser.stop()
        log.info("浏览器已关闭")
    return summaries


def cli():
    parser = argparse.ArgumentParser(description="贝壳在售直达采集 MVP（URL 直达，无搜索/登录）")
    parser.add_argument(
        "--community-id",
        type=int,
        nargs="+",
        default=[],
        help="小区 ID（按 community_platform_pages 的 ke 入口查找），可多个",
    )
    parser.add_argument(
        "--listing-url",
        default=None,
        help="直接传入挂牌列表页 URL 调试（跳过库内查找）",
    )
    parser.add_argument(
        "--area",
        type=float,
        default=None,
        help="可选：面积筛选值（㎡）；默认不做面积筛选，整页直捞",
    )
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="指令必带：首页打开后人工过验证码 / 登录，回车确认后开始采集。",
    )
    parser.add_argument(
        "--debug",
        "--excel",
        dest="debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML（兼容旧参数 --excel）。",
    )
    args = parser.parse_args()
    if not args.community_id and not args.listing_url:
        parser.error("至少提供 --community-id 或 --listing-url 之一")
    uc.loop().run_until_complete(
        main(
            community_ids=args.community_id,
            listing_url=args.listing_url,
            area=args.area,
            manual_login=args.manual_login,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    cli()
