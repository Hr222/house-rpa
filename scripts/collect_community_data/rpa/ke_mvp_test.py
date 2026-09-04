# -*- coding: utf-8 -*-
"""贝壳 MVP 测试脚本（URL 直达抓取形式）。

抓取形式（2026-09-03 整改，对齐 ajk/lyj 模板）：小区挂牌入口已由统一
初始化工具存入 community_platform_pages（/ershoufang/c{ID}），本脚本按
community_id 直达挂牌列表页，不走"首页 → 搜索 → 面积筛选"的模拟
点击链路；读 HTML 解析在售快照（工程 parser）。

流程：读取库内 ke 入口 → 首页先行建立会话（--manual-login 时人工
过验证码/登录）→ page.get(listing_page_url) 直达 → 风控协议（工程件：
wait_and_reload_after_block + _platform.detect_block，命中拦截 →
置前 + 暂停等人工 → 回车 → 复检）→ 读 HTML 解析 → /ershoufang/
分页 URL 直达翻页累加。

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
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from html import unescape
from urllib.parse import urlparse, urljoin

import nodriver as uc
from nodriver.core import util as nodriver_util

from app.rpa.core import config
from app.rpa.core.status import PlatformResultStatus
from app.rpa.platforms import KePlatformAdapter
from app.rpa.platforms.base import _human_click, wait_and_reload_after_block, has_matching_community_snapshots
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
# 解析：在售分页信息（page-data）
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


# ============================================================
# 可选面积筛选（--area 时才使用，默认不筛选）
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
# 库内入口查找与翻页 URL 推导
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


def build_page_urls(listing_url: str, first_page_html: str) -> list[str]:
    """由第 1 页分页链接收集后续页 URL（含本小区 c{ID} 的 pg{N} 链接，无点击）。

    贝壳小区列表翻页链接携带本小区 token（如 /ershoufang/pg2c{ID}/），
    只收录包含同一 token 的链接，避免误采全市/商圈翻页；无分页链接
    则返回空列表（单页小区）。
    """
    parsed = urlparse(listing_url)
    origin = f"{parsed.scheme}://{parsed.hostname}"
    token_m = re.search(r"/ershoufang/(c\d+)", parsed.path)
    if token_m is None:
        return []
    token = token_m.group(1)

    page_urls: list[str] = []
    seen_pages: set[int] = set()
    for match in re.finditer(r'href="([^"]*pg(\d+)[^"]*)"', first_page_html or ""):
        href = urljoin(listing_url, unescape(match.group(1)))
        path = urlparse(href).path
        num_m = re.search(r"pg(\d+)", path)
        if num_m is None or token not in path:
            continue
        page_no = int(num_m.group(1))
        if page_no in seen_pages or page_no < 2 or page_no > MAX_PAGES:
            continue
        seen_pages.add(page_no)
        page_urls.append(href)
    return page_urls


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
    debug: bool,
) -> CommunityCollection:
    """单小区直达采集：导航挂牌页 → 风控协议 →（可选）面积筛选 →
    读 HTML 解析在售快照（含 URL 直达翻页）。

    返回原始 CommunityCollection（一个对象，两个 list）；贝壳不采成交、
    不采小区均价（deals 与 community_avg_price 恒空）；筛选计算在编排层。
    """
    listing_url = target["listing_page_url"]
    await tab.get(listing_url)
    await tab
    await asyncio.sleep(3)

    # 第 1 页：走工程风控协议，风控未解除前不会返回
    html = await wait_and_reload_after_block(
        tab, _platform.detect_block, f"小区挂牌页[{target['community_name']}]"
    )
    if debug:
        await dump_html(tab, f"ke_listing_{target['community_id'] or 'direct'}_p1")

    # 空态识别：调用子平台 adapter 导出的判空能力（与 detect_block 同级，
    # 真实空态页见 _platform.is_no_result docstring）。命中即在售 0 条，
    # 直接 NO_DATA 返回，不解析不翻页（空态页下方是别的小区推荐位）
    if _platform.is_no_result(html):
        log.info("[空态] %s 在贝壳在售 0 条（跳过推荐位）", target["community_name"])
        return CommunityCollection(
            platform="ke",
            community_id=target["community_id"],
            community_name=target["community_name"],
            listing_page_url=listing_url,
            deal_page_url=None,  # 贝壳不采成交
            status=PlatformResultStatus.NO_DATA,
            blocked_reason=None,
            listings=[],
            deals=[],  # 贝壳不采成交记录
            community_avg_price=None,  # 贝壳不采小区均价
        )

    area_confirmed = True
    if area is not None:
        area_confirmed = await _apply_area_filter(tab, area, area)
        if not area_confirmed:
            log.warning("[筛选] 面积筛选未成功提交，本次为全量在售")
        # 面积筛选（如有）会刷新页面，重新读取当前 HTML
        html = await tab.get_content()

    snapshots = _platform.parse_listing_snapshots(html)

    # 页面归属校验（工程件，与 ajk/lyj 模板同款）：解析出的快照必须
    # 能与目标小区名匹配，否则判定入口落地页错误，整页弃用不入库
    if snapshots and not has_matching_community_snapshots(snapshots, target["community_name"]):
        log.warning(
            "[归属不符] %s：%s 页面快照与目标小区不匹配（%d 条全部弃用）",
            target["community_name"],
            listing_url,
            len(snapshots),
        )
        snapshots = []

    for snapshot in snapshots:
        log.info(
            "[在售] %s | %s | %s㎡ | 总价 %s万 | 单价 %s元/㎡",
            snapshot.community_name or "-",
            snapshot.layout or "-",
            snapshot.area or "-",
            snapshot.total_price or "-",
            snapshot.unit_price or "-",
        )

    # URL 直达翻页：仅归属校验通过才翻页
    page_urls = build_page_urls(listing_url, html) if snapshots else []
    if page_urls:
        log.info("[翻页] 第 1 页 %d 条；另有 %d 页待采", len(snapshots), len(page_urls))
    for page_no, page_url in enumerate(page_urls, start=2):
        await tab.get(page_url)
        await tab
        await asyncio.sleep(2)
        # 每页同样走工程风控协议：命中拦截暂停等人工，干净后才解析
        page_html = await wait_and_reload_after_block(
            tab, _platform.detect_block, f"翻页第 {page_no} 页"
        )
        if debug:
            await dump_html(tab, f"ke_listing_{target['community_id'] or 'direct'}_p{page_no}")
        page_snapshots = _platform.parse_listing_snapshots(page_html)
        snapshots.extend(page_snapshots)
        if not page_snapshots:
            # 空页 = 已翻过真实在售末页，停止翻页
            log.info("[翻页] 第 %d 页无在售，停止翻页", page_no)
            break
        log.info("[翻页] 第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_snapshots), len(snapshots))

    log.info(
        "[采集汇总] %s：在售 %d 条（贝壳不采成交与均价）",
        target["community_name"],
        len(snapshots),
    )

    return CommunityCollection(
        platform="ke",
        community_id=target["community_id"],
        community_name=target["community_name"],
        listing_page_url=listing_url,
        deal_page_url=None,  # 贝壳不采成交
        status=PlatformResultStatus.SUCCESS if snapshots else PlatformResultStatus.NO_DATA,
        blocked_reason=None,  # 风控协议：拦截在页内暂停等人工，返回即干净
        listings=snapshots,
        deals=[],  # 贝壳不采成交记录
        community_avg_price=None,  # 贝壳不采小区均价
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
                debug=debug,
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
