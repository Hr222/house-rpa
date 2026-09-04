# -*- coding: utf-8 -*-
"""链家 MVP 测试脚本（URL 直达抓取形式）。

抓取形式（2026-09-04 整改，对齐 ke 模板）：小区挂牌与成交入口已由
统一初始化工具（scripts/initialize_community_page/）存入
community_platform_pages（listing_page_url=/ershoufang/c{ID}、
deal_page_url=/chengjiao/c{ID}），本脚本按 community_id 直达两个入口。

流程：读取库内 lj 入口（挂牌+成交）→ 首页先行建立会话（--manual-login 时
人工过验证码/登录）→ 采集主流程委托工程 collector.collect_listing_by_url
（与工程 shell 同一函数：风控协议、空态识别、归属校验、在售/成交 URL
直达翻页全部单源；成交为真实明细全量）；MVP 只做 PlatformResult →
CommunityCollection 映射与结果打印。--area 面积过滤改用工程 selection
（面积口径已剥离出 RPA，见 base.py 说明）。

链家成交是本平台特有采集（ajk/ke/lyj 均无）：成交记录全部真实明细入库
（不做面积/近半年裁剪，原始数据语义，筛选在编排层）。浏览器全程存活，
抓完不主动关闭；回车确认 / Ctrl+C 结束时关闭浏览器窗口。

用法：
  python -m scripts.collect_community_data.rpa.lj_mvp_test --community-id 5650
  python -m scripts.collect_community_data.rpa.lj_mvp_test --listing-url "https://sz.lianjia.com/ershoufang/c241106475..." --area 89.5
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
from app.rpa.platforms import LjPlatformAdapter
from app.rpa.platforms.base import wait_and_reload_after_block
from app.rpa.platforms.lj import collector as lj_adapter
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging
from scripts.collect_community_data.models import CommunityCollection


setup_logging()
log = logging.getLogger("lj-mvp-test")
_platform = LjPlatformAdapter()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PAGES_DB = PROJECT_ROOT / "persist" / "property_records.sqlite3"

# 链家深圳二手房首页：先开首页建立会话，再逐个直达小区挂牌/成交页
START_URL = "https://sz.lianjia.com/ershoufang/"

# 翻页防失控兜底：单小区最大翻页数（每页 30 条，50 页 = 1500 条上限）
MAX_PAGES = 50

# 常驻浏览器引用：批次结束保持存活（atexit 摘除，脚本退出不关闭）；
# 终端 Ctrl+C 终止时由 terminate_browser() 同步关闭——事件循环被冻结，
# MVP 协程的 finally 无法自行收尾，必须在进程退出层处理
_browser = None


async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


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
# 库内入口查找
# ============================================================

def resolve_listing_urls(community_ids: list[int]) -> list[dict]:
    """从房源记录库读取链家已初始化的挂牌与成交入口。

    白名单语义：community_platform_pages 里没有 lj 入口的小区直接跳过。
    链家成交是真实成交（全量明细入库），deal_page_url 一并取出。
    """
    con = sqlite3.connect(f"file:{PAGES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    targets: list[dict] = []
    for community_id in community_ids:
        row = con.execute(
            "SELECT community_id, source_community_name, listing_page_url, deal_page_url "
            "FROM community_platform_pages WHERE community_id = ? AND source_platform = 'lj'",
            (community_id,),
        ).fetchone()
        if row is None or not row["listing_page_url"]:
            log.warning("[跳过] 小区 %s 无链家挂牌入口（未初始化）", community_id)
            continue
        targets.append(
            {
                "community_id": community_id,
                "community_name": row["source_community_name"],
                "listing_page_url": row["listing_page_url"],
                # 成交入口可能存在缺失（部分老记录只有挂牌）；缺失时跳过成交段
                "deal_page_url": row["deal_page_url"],
            }
        )
    con.close()
    return targets


def print_listing_snapshots(snapshots: list):
    if not snapshots:
        print("链家: 未抓到在售房源摘要")
        return
    for item in snapshots:
        print(
            "链家: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )
        _url = item.listing_url or "-"
        print("  房源链接:", _url if len(_url) <= 72 else _url[:69] + "...")


def print_deal_records(deals: list[dict]):
    if not deals:
        print("链家: 未抓到成交记录")
        return
    print("链家成交明细（真实成交，全量）:")
    for item in deals:
        print(
            f"  {item.get('area') or '-'}㎡ | {item.get('date') or '-'} | "
            f"{item.get('total_price') or '-'}万 | {item.get('price') or '-'}元/平"
        )


# ============================================================
# 单小区直达采集（在售 + 成交）
# ============================================================

async def collect_community(
    tab,
    *,
    target: dict,
    area: Optional[float],
) -> CommunityCollection:
    """单小区直达采集：委托工程 collector.collect_listing_by_url（与工程
    shell 同一函数），风控协议、空态识别、归属校验、在售/成交 URL 直达
    翻页全部单源；MVP 只做 PlatformResult → CommunityCollection 映射与
    日志打印。

    链家成交是真实成交事实，deal_page_url 存在即由工程采集器独立采集
    （挂牌空态不影响成交事实采集）。--area 时用工程 selection 做展示
    过滤（面积口径已剥离出 RPA）。
    """
    result = await lj_adapter.collect_listing_by_url(
        page=tab,
        community_name=target["community_name"],
        listing_page_url=target["listing_page_url"],
        deal_page_url=target.get("deal_page_url"),
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

    deals = list(result.deal_records)
    if deals:
        print_deal_records(deals)

    log.info(
        "[采集汇总] %s：在售 %d 条，真实成交 %d 条",
        target["community_name"],
        len(listings),
        len(deals),
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
        platform="lj",
        community_id=target["community_id"],
        community_name=target["community_name"],
        listing_page_url=target["listing_page_url"],
        deal_page_url=target.get("deal_page_url"),  # 白名单行带入（缺失则 None）
        status=result.status,
        blocked_reason=blocked_reason,
        listings=listings,
        deals=deals,  # 真实成交明细（仅 lj/fang 有）
        community_avg_price=None,  # 链家不采小区均价
    )


async def main(
    community_ids: list[int],
    listing_url: Optional[str],
    area: Optional[float],
    manual_login: bool,
    debug: bool,
    deal_url: Optional[str] = None,
) -> list[CommunityCollection]:
    """常驻采集进程：一个浏览器会话逐小区抓取在售与真实成交。

    返回逐小区原始 CommunityCollection（一个对象，两个 list；筛选计算在
    编排层）。首批清单采集完后进程不退出：模拟工程 runtime 常驻——等待
    人工回车/Ctrl+C/关闭浏览器窗口后结束。deal_url 仅直传 URL 调试用
    （编排层以关键字调用本 main，不受新增默认参数影响）。
    """
    if debug:
        set_debug_mode(True)

    targets: list[dict] = []
    if listing_url:
        targets.append(
            {
                "community_id": None,
                "community_name": "直传URL",
                "listing_page_url": listing_url,
                "deal_page_url": deal_url,
            }
        )
    targets.extend(resolve_listing_urls(community_ids))
    if not targets:
        print("[错误] 没有可采集目标：--community-id 未命中链家入口，且未传 --listing-url")
        return []
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
    global _browser
    _browser = browser
    # 浏览器常驻：把实例从 nodriver 的 atexit 清理注册表移除，
    # 批次结束/脚本退出都不关闭浏览器（对齐工程 runtime 的常驻模式）
    nodriver_util.__registered__instances__.discard(browser)

    # 先开链家首页建立会话：指令必带 manual_login，首页先人工过
    # 验证码/登录，回车确认；再由工程风控件复检，干净后逐个直达小区
    tab = await browser.get(START_URL)
    await tab
    await asyncio.sleep(3)
    if manual_login:
        await wait_for_manual_login()
    await wait_and_reload_after_block(tab, _platform.detect_block, "链家首页")

    summaries: list[CommunityCollection] = []
    try:
        for target in targets:
            item = await collect_community(
                tab,
                target=target,
                area=area,
            )
            summaries.append(item)

        # 采集完成：浏览器保持打开，等人工回车确认结束
        # （回车 / Ctrl+C 均会关闭浏览器窗口；期间人工关闭浏览器也会直接结束）。
        # 必须用单个 daemon 线程等回车：wait_for+to_thread 每次超时会留下
        # 堵在 stdin 的孤儿线程——回车被孤儿吃掉、Ctrl+C 后进程也因非
        # daemon 线程无法退出（2026-09-03 ke 实测教训），这种写法不可用
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
            await dump_html(tab, "lj_error")
        log.exception("测试异常中断")
        raise
    finally:
        # 结束即关闭浏览器窗口：回车确认 / Ctrl+C / 出错 均覆盖。
        # Ctrl+C 终止时事件循环可能已停，先同步终止浏览器进程
        # （不依赖事件循环），再走 stop 收尾连接与临时目录。
        process = getattr(browser, "process", None)
        if process is not None:
            try:
                process.terminate()
            except Exception:
                log.debug("浏览器进程终止失败（可能已退出）")
        browser.stop()
        log.info("浏览器已关闭")
    return summaries


def cli():
    parser = argparse.ArgumentParser(
        description="链家在售+真实成交直达采集 MVP（URL 直达，无搜索/登录；成交为真实明细全量）"
    )
    parser.add_argument(
        "--community-id",
        type=int,
        nargs="+",
        default=[],
        help="小区 ID（按 community_platform_pages 的 lj 入口查找挂牌+成交），可多个",
    )
    parser.add_argument(
        "--listing-url",
        default=None,
        help="直接传入挂牌列表页 URL 调试（跳过库内查找；成交入口需 --deal-url 配套）",
    )
    parser.add_argument(
        "--deal-url",
        default=None,
        help="直接传入成交列表页 URL 调试（配合 --listing-url 使用）",
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
    listing_url = args.listing_url
    try:
        uc.loop().run_until_complete(
            main(
                community_ids=args.community_id,
                listing_url=listing_url,
                area=args.area,
                manual_login=args.manual_login,
                debug=args.debug,
                deal_url=args.deal_url,
            )
        )
    except KeyboardInterrupt:
        # 终端 Ctrl+C：事件循环已冻结，MVP 协程的 finally 不会执行，
        # 在进程退出层同步终止常驻浏览器
        terminate_browser()


if __name__ == "__main__":
    cli()
