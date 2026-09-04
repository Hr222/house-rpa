# -*- coding: utf-8 -*-
"""乐有家 MVP 测试脚本（URL 直达抓取形式）。

抓取形式（2026-09-03 整改，对齐 ajk 模板）：小区挂牌入口已由统一初始化
工具存入 community_platform_pages（/esf?b={小区ID}），本脚本按
community_id 直达挂牌列表页，不走"首页 → 搜索 → 面积筛选"的模拟
点击链路；读 HTML 解析在售快照与小区均价。

流程：读取库内 lyj 入口 → 首页先行建立会话（--manual-login 时人工
过验证码/登录）→ page.get(listing_page_url) 直达 → 风控协议（工程件：
wait_and_reload_after_block + lyj_adapter.detect_block，命中拦截 →
置前 + 暂停等人工 → 回车 → 复检）→ 读 HTML 解析 → /esf/n{page}/
URL 直达翻页累加。

乐有家无成交记录（与 ajk 同口径，deals 恒空）；面积筛选链路保留为
可选（--area，默认不筛选，整页直捞）。浏览器全程存活，抓完不主动
关闭；回车确认 / Ctrl+C 结束时关闭浏览器窗口。

用法：
  python -m scripts.collect_community_data.rpa.lyj_mvp_test --community-id 5650
  python -m scripts.collect_community_data.rpa.lyj_mvp_test --listing-url "https://shenzhen.leyoujia.com/esf?b=539" --area 89.5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

import nodriver as uc
from nodriver.core import util as nodriver_util

from app.rpa.core import config
from app.rpa.core.models import ListingSnapshot
from app.rpa.core.status import PlatformResultStatus
from app.rpa.platforms.adapters import lyj as lyj_adapter
from app.rpa.platforms.base import wait_and_reload_after_block, has_matching_community_snapshots
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging
from scripts.collect_community_data.models import CommunityCollection


setup_logging()
log = logging.getLogger("lyj-mvp-test")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PAGES_DB = PROJECT_ROOT / "persist" / "property_records.sqlite3"

# 乐有家深圳二手房首页：先开首页建立会话，再逐个直达小区列表页
START_URL = "https://shenzhen.leyoujia.com/esf/"

# 翻页防失控兜底：单小区最大翻页数（每页 60 条，50 页 = 3000 条上限）
MAX_PAGES = 50

# 常驻浏览器引用：批次结束保持存活（atexit 摘除，脚本退出不关闭）；
# 终端 Ctrl+C 终止时由 terminate_browser() 同步关闭——事件循环被冻结，
# MVP 协程的 finally 无法自行收尾，必须在进程退出层处理
_browser = None


async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


async def wait_for_manual_login():
    """风控交互：暂停等人工在浏览器处理，回车确认后继续。"""
    prompt = (
        "\n请在打开的浏览器里手动完成验证码 / 登录。"
        "\n完成后回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


# ============================================================
# 交互工具：真人点击（仅供可选的面积筛选使用）
# ============================================================

async def is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def human_click(page, element, label: str) -> bool:
    if not element:
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
            await asyncio.sleep(1.0)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s click failed: %s", label, last_error)
    return False


# ============================================================
# 可选面积筛选（--area 时才使用，默认不筛选）
# ============================================================

async def fill_area_inputs(page, area_min, area_max):
    """乐有家面积筛选：找到"面积"区 → 点"更多及自定义" → 填值 → 点确定。"""
    try:
        containers = await page.select_all("div.selected-index.hasmore", timeout=3)
    except Exception:
        containers = []

    area_container = None
    for c in containers:
        try:
            tit = await c.apply(
                "(el) => { const t = el.querySelector('.c333.tit'); return t ? t.textContent.trim() : ''; }"
            )
        except Exception:
            tit = ""
        if tit == "面积":
            area_container = c
            break

    if area_container is None:
        raise RuntimeError("未找到面积筛选区（标题为'面积'的 hasmore 容器）")

    try:
        btns = await area_container.query_selector_all("span.btn-showmore")
    except Exception:
        btns = []
    if btns:
        log.info("[3] 点击面积区的'更多及自定义'展开")
        await human_click(page, btns[0], "btn-showmore")
        await page
        await asyncio.sleep(2)

    try:
        min_el = await page.select("#a_start", timeout=3)
    except Exception:
        min_el = None
    try:
        max_el = await page.select("#a_end", timeout=3)
    except Exception:
        max_el = None

    if min_el is None or max_el is None:
        log.warning("[3] 展开后未找到 #a_start / #a_end")
        return False

    min_ok = await is_interactable(min_el)
    max_ok = await is_interactable(max_el)
    log.info("[3] 面积输入框: #a_start 可交互=%s, #a_end 可交互=%s", min_ok, max_ok)

    if not min_ok and not max_ok:
        log.warning("[3] 面积输入框不可交互，可能展开失败")
        return False

    if min_ok:
        await human_click(page, min_el, "area min input")
        try:
            await min_el.clear_input()
        except Exception:
            pass
        await asyncio.sleep(0.3)
        await min_el.send_keys(str(int(area_min)))
        await page
        await asyncio.sleep(0.5)
        log.info("[3] 填入下限: %s", area_min)

    if max_ok:
        await human_click(page, max_el, "area max input")
        try:
            await max_el.clear_input()
        except Exception:
            pass
        await asyncio.sleep(0.3)
        await max_el.send_keys(str(int(area_max)))
        await page
        await asyncio.sleep(0.8)
        log.info("[3] 填入上限: %s", area_max)

    try:
        confirm_btn = await page.select("#areaUsedefinedBtn", timeout=3)
    except Exception:
        confirm_btn = None

    confirm_clicked = False
    if confirm_btn:
        confirm_clicked = await human_click(page, confirm_btn, "area confirm")
    if not confirm_clicked and max_el and max_ok:
        try:
            await max_el.send_keys("\r")
            await page
            confirm_clicked = True
            log.info("[3] 用回车兜底提交")
        except Exception:
            pass

    await page
    await asyncio.sleep(3)
    return confirm_clicked


# ============================================================
# 解析：在售快照（截断到"猜你喜欢"之前）与小区均价（社区信息卡）
# ============================================================

def _normalize_text(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_listing_snapshots(html: str) -> list[ListingSnapshot]:
    """从乐有家列表页提取房源快照。

    每个房源在 <li class="item clearfix"> 内：
      p.tit a              → 标题
      p.attr span          → "3室2厅1卫 / 建筑面积73.5㎡"
      p.attr a[href*="/xq/detail"]  → 小区名链接
      span.salePrice       → 总价数字
      p.sub                → "单价44218元/㎡"
    排除猜你喜欢/推荐位 — 截断到"猜你喜欢"之前。
    """
    # 截断到尾页/猜你喜欢之前
    cut = html.find("猜你喜欢")
    source = html[:cut] if cut > 0 else html

    snapshots = []
    # 匹配 <li class="item clearfix" ...> ... </li>
    for block in re.finditer(
        r'<li class="item clearfix"[^>]*>(.*?)</li>', source, re.S
    ):
        chunk = block.group(1)

        # 小区名：p.attr 中的 a（/xq/detail/xxx 或带 font 包裹）
        community_name = None
        comm_m = re.search(r'href="/xq/detail/\d+[^"]*"[^>]*>(?:<[^>]+>)*\s*([^<]+)', chunk)
        if comm_m:
            community_name = _normalize_text(comm_m.group(1))

        # 户型：p.attr 中的第一组数字室数字厅
        layout = None
        layout_m = re.search(r"(\d+室\d+厅)", chunk)
        if layout_m:
            layout = layout_m.group(1)

        # 面积：建筑面积XX.XX㎡
        area = None
        area_m = re.search(r"建筑面积\s*([\d.]+)\s*㎡", chunk)
        if area_m:
            area = float(area_m.group(1))

        # 总价(万): span.salePrice
        total_price = None
        tp_m = re.search(r'salePrice[^>]*>\s*([\d,]+)\s*<', chunk)
        if tp_m:
            total_price = float(tp_m.group(1).replace(",", ""))

        # 单价: p.sub 中的 "单价44218元/㎡"
        unit_price = None
        up_m = re.search(
            r'<p class="sub">.*?([\d,]+)\s*元\s*/?\s*㎡',
            chunk,
        )
        if up_m:
            unit_price = float(up_m.group(1).replace(",", ""))

        if unit_price is None and total_price is None:
            continue

        snapshots.append(
            ListingSnapshot(
                house_id="",
                community_name=community_name,
                area=area,
                layout=layout,
                unit_price=unit_price,
                total_price=total_price,
            )
        )
    return snapshots


def print_listing_snapshots(snapshots: list[ListingSnapshot]):
    if not snapshots:
        print("乐有家: 未抓到房源摘要")
        return
    for item in snapshots:
        print(
            "乐有家: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )


def parse_community_avg_price(html: str) -> Optional[float]:
    """从结果页社区信息卡提取小区均价。

    DOM: <em class="txt">54386元/㎡</em>
    乐有家无成交记录，业务上用小区均价顶替 deal_prices。
    """
    m = re.search(r"小区均价</em>\s*<em\s[^>]*>\s*([\d,]+)\s*元", html)
    return float(m.group(1).replace(",", "")) if m else None


# ============================================================
# 库内入口查找与翻页 URL 推导
# ============================================================

def resolve_listing_urls(community_ids: list[int]) -> list[dict]:
    """从房源记录库读取乐有家已初始化的挂牌入口。

    白名单语义：community_platform_pages 里没有 lyj 入口的小区直接跳过。
    """
    con = sqlite3.connect(f"file:{PAGES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    targets: list[dict] = []
    for community_id in community_ids:
        row = con.execute(
            "SELECT community_id, source_community_name, listing_page_url "
            "FROM community_platform_pages WHERE community_id = ? AND source_platform = 'lyj'",
            (community_id,),
        ).fetchone()
        if row is None or not row["listing_page_url"]:
            log.warning("[跳过] 小区 %s 无乐有家挂牌入口（未初始化）", community_id)
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
    """由第 1 页分页链接收集后续页 URL（/esf/n{N}/…，取页面原生链接 urljoin，无点击）。

    分页 href 自带站点当前的查询串（b= 或 c= 等），urljoin 还原绝对地址
    即与当前入口上下文一致；无分页链接则返回空列表（单页小区）。
    """
    page_urls: list[str] = []
    seen_pages: set[int] = set()
    for match in re.finditer(r'href="([^"]*/esf/n(\d+)/[^"]*)"', first_page_html or ""):
        href = urljoin(listing_url, unescape(match.group(1)))
        page_no = int(match.group(2))
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
    读 HTML 解析原始快照与小区均价（含 URL 直达翻页）。

    返回原始 CommunityCollection（一个对象，两个 list）；乐有家无成交
    记录，deals 恒空；筛选计算在编排层。
    """
    listing_url = target["listing_page_url"]
    await tab.get(listing_url)
    await tab
    await asyncio.sleep(3)

    # 第 1 页：走工程风控协议，风控未解除前不会返回
    html = await wait_and_reload_after_block(
        tab, lyj_adapter.detect_block, f"小区挂牌页[{target['community_name']}]"
    )
    if debug:
        await dump_html(tab, f"lyj_listing_{target['community_id'] or 'direct'}_p1")

    # 空态识别：调用子平台 adapter 导出的判空能力（与 detect_block 同级）。
    # 零在售小区页面为空态 + 其他小区推荐位，绝不能把推荐位当在售解析；
    # marker 真实性待编排层实测核对（见 lyj_adapter.is_no_result docstring）
    if lyj_adapter.is_no_result(html):
        log.info(
            "[空态] %s 在乐有家在售 0 条（页面为无房源空态，跳过推荐位）",
            target["community_name"],
        )
        return CommunityCollection(
            platform="lyj",
            community_id=target["community_id"],
            community_name=target["community_name"],
            listing_page_url=listing_url,
            deal_page_url=None,
            status=PlatformResultStatus.NO_DATA,
            blocked_reason=None,
            listings=[],
            deals=[],  # 乐有家无成交记录
            community_avg_price=None,
        )

    area_confirmed = True
    if area is not None:
        area_confirmed = await fill_area_inputs(tab, area, area)
        if not area_confirmed:
            log.warning("[筛选] 面积筛选未成功提交，本次为全量在售")
        # 面积筛选（如有）会刷新页面，重新读取当前 HTML
        html = await tab.get_content()

    snapshots = parse_listing_snapshots(html)
    community_avg_price = parse_community_avg_price(html)

    # 页面归属校验（工程件，与 URL 初始化 MVP 同款）：解析出的快照必须
    # 能与目标小区名匹配，否则判定入口落地页错误，整页弃用不入库
    # （如泰福名苑的 lyj 入口 b=861860 落地非本小区页面，需人工核修）。
    if snapshots and not has_matching_community_snapshots(snapshots, target["community_name"]):
        log.warning(
            "[归属不符] %s：%s 页面快照与目标小区不匹配（%d 条全部弃用）",
            target["community_name"],
            listing_url,
            len(snapshots),
        )
        snapshots = []
        community_avg_price = None

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
            tab, lyj_adapter.detect_block, f"翻页第 {page_no} 页"
        )
        if debug:
            await dump_html(tab, f"lyj_listing_{target['community_id'] or 'direct'}_p{page_no}")
        page_snapshots = parse_listing_snapshots(page_html)
        snapshots.extend(page_snapshots)
        if not page_snapshots:
            # 空页 = 已翻过真实在售末页，停止翻页
            log.info("[翻页] 第 %d 页无在售，停止翻页", page_no)
            break
        log.info("[翻页] 第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_snapshots), len(snapshots))

    log.info(
        "[采集汇总] %s：在售 %d 条，小区均价 %s 元/㎡",
        target["community_name"],
        len(snapshots),
        f"{community_avg_price:.0f}" if community_avg_price else "未识别",
    )

    return CommunityCollection(
        platform="lyj",
        community_id=target["community_id"],
        community_name=target["community_name"],
        listing_page_url=listing_url,
        deal_page_url=None,  # 乐有家无成交页
        status=PlatformResultStatus.SUCCESS if snapshots else PlatformResultStatus.NO_DATA,
        blocked_reason=None,  # 风控协议：拦截在页内暂停等人工，返回即干净
        listings=snapshots,
        deals=[],  # 乐有家无成交记录
        community_avg_price=community_avg_price,
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
        print("[错误] 没有可采集目标：--community-id 未命中乐有家入口，且未传 --listing-url")
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
    global _browser
    _browser = browser
    # 浏览器常驻：把实例从 nodriver 的 atexit 清理注册表移除，
    # 批次结束/脚本退出都不关闭浏览器（对齐工程 runtime 的常驻模式）
    nodriver_util.__registered__instances__.discard(browser)

    # 先开乐有家首页建立会话：指令必带 manual_login，首页先人工过
    # 验证码/登录，回车确认；再由工程风控件复检，干净后逐个直达小区
    tab = await browser.get(START_URL)
    await tab
    await asyncio.sleep(3)
    if manual_login:
        await wait_for_manual_login()
    await wait_and_reload_after_block(tab, lyj_adapter.detect_block, "乐有家首页")

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
        # （单线程阻塞等回车，不用轮询/超时——避免输入线程堆积吃掉回车）
        log.info("采集完成，浏览器保持打开")
        await asyncio.to_thread(
            input, "\n按回车结束脚本并关闭浏览器窗口...\n"
        )
    except Exception:
        if tab is not None:
            await dump_html(tab, "lyj_error")
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
    parser = argparse.ArgumentParser(description="乐有家在售直达采集 MVP（URL 直达，无搜索/登录）")
    parser.add_argument(
        "--community-id",
        type=int,
        nargs="+",
        default=[],
        help="小区 ID（按 community_platform_pages 的 lyj 入口查找），可多个",
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
