# -*- coding: utf-8 -*-
"""房天下 MVP 测试脚本（URL 直达抓取形式）。

抓取形式（2026-09-03 整改，对齐 lj 模板）：小区挂牌与成交入口已由统一
初始化工具（scripts/initialize_community_page/）存入
community_platform_pages（listing_page_url=/house-xm{id}、
deal_page_url=/loupan/{id}/chengjiao，二者同 ID），本脚本按 community_id
直达两个入口，不再走"首页 → 搜索 → 面积筛选 → 点详情 → 点成交"的模拟
点击链路；直接读取 HTML 解析在售快照与真实成交记录。

流程：读取库内 fang 入口（挂牌+成交）→ 首页先行建立会话（--manual-login
时人工过验证码/登录）→ page.get(listing_page_url) 直达在售聚合页 →
风控协议（工程件 wait_and_reload_after_block + _platform.detect_block）
→ 空态判定（统一调子平台 adapter 能力 _platform.is_no_result，与其余
4 平台同级）→ 归属校验 → URL 直达翻页（原生分页链接收集）→
page.get(deal_page_url) 直达成交页 → 风控协议 → 解析真实成交明细
（工程 parsers.fang.parse_deal_records）→ 成交翻页（优先 URL 直达）。

房天下成交是本平台特有采集（与 lj 一致）：成交记录全部真实明细入库
（不做面积/近半年裁剪，原始数据语义，筛选在编排层）。面积筛选链路保留
为可选（--area，默认不筛选，整页直捞）。浏览器全程存活，抓完不主动
关闭；回车确认 / Ctrl+C 结束时关闭浏览器窗口。

用法：
  python -m scripts.collect_community_data.rpa.fang_mvp_test --community-id 5650
  python -m scripts.collect_community_data.rpa.fang_mvp_test --listing-url "https://sz.esf.fang.com/house-xm2810138134" --area 89.5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
import threading
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import nodriver as uc
from nodriver.core import util as nodriver_util

from app.rpa.core import config
from app.rpa.core.models import ListingSnapshot
from app.rpa.core.status import PlatformResultStatus
from app.rpa.platforms.fang import parser as parsers
from app.rpa.platforms import FangPlatformAdapter
from app.rpa.platforms.base import (
    wait_and_reload_after_block,
    has_matching_community_snapshots,
)
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging
from scripts.collect_community_data.models import CommunityCollection


setup_logging()
log = logging.getLogger("fang-mvp-test")
_platform = FangPlatformAdapter()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PAGES_DB = PROJECT_ROOT / "persist" / "property_records.sqlite3"

# 房天下深圳二手房首页：先开首页建立会话，再逐个直达小区挂牌/成交页
START_URL = "https://sz.esf.fang.com/"

# 翻页防失控兜底：单小区最大翻页数
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
# 交互工具：真人点击（仅供可选的面积筛选与成交翻页回退使用）
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

async def fill_area_inputs(page, area_min, area_max) -> bool:
    """房天下面积筛选：定位 li[name='customarea'] 填 cminArea/cmaxArea → 确定。

    与老 MVP / 工程 adapter 同款（房天下需自定义输入，非预设档位）。
    """
    try:
        container = await page.select("li[name='customarea']", timeout=3)
    except Exception:
        container = None
    if container is None:
        log.warning("[3] 未找到面积筛选区（li[name='customarea']），本次为全量在售")
        return False

    try:
        min_inputs = await container.query_selector_all("input#cminArea")
        max_inputs = await container.query_selector_all("input#cmaxArea")
    except Exception:
        min_inputs, max_inputs = [], []
    if not min_inputs or not max_inputs:
        log.warning("[3] 面积筛选区未找到 cminArea/cmaxArea，本次为全量在售")
        return False
    min_el, max_el = min_inputs[0], max_inputs[0]

    await human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)

    await human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)

    confirm_clicked = False
    try:
        confirms = await container.query_selector_all("#aConfirmButton")
    except Exception:
        confirms = []
    if confirms:
        confirm_clicked = await human_click(page, confirms[0], "area confirm")
    if not confirm_clicked:
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
# 库内入口查找与翻页 URL 推导
# ============================================================

def resolve_listing_urls(community_ids: list[int]) -> list[dict]:
    """从房源记录库读取房天下已初始化的挂牌与成交入口。

    白名单语义：community_platform_pages 里没有 fang 入口的小区直接跳过。
    房天下成交是真实成交（全量明细入库），deal_page_url 一并取出。
    """
    con = sqlite3.connect(f"file:{PAGES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    targets: list[dict] = []
    for community_id in community_ids:
        row = con.execute(
            "SELECT community_id, source_community_name, listing_page_url, deal_page_url "
            "FROM community_platform_pages WHERE community_id = ? AND source_platform = 'fang'",
            (community_id,),
        ).fetchone()
        if row is None or not row["listing_page_url"]:
            log.warning("[跳过] 小区 %s 无房天下挂牌入口（未初始化）", community_id)
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


def _id_token_from_url(page_url: str) -> Optional[str]:
    """从房天下挂牌/成交入口 URL 提取小区数字 ID。

    挂牌 /house-xm{ID}（house-xm 后直接跟数字，无斜杠）、
    成交 /loupan/{ID}/chengjiao 共用同一 ID（初始化工具已人工核对），
    翻页链接与小区 token 都用该 ID 过滤。
    """
    m = re.search(r"/(?:house-xm)(\d+)", page_url or "") or re.search(
        r"/loupan/(\d+)", page_url or ""
    )
    return m.group(1) if m else None


def build_page_urls(page_url: str, first_page_html: str) -> list[str]:
    """由第 1 页原生分页链接收集后续页 URL（URL 直达无点击）。

    挂牌页与成交页通用：房天下分页链接携带小区数字 ID
    （在售 /house-xm{ID}/i3{N}/、成交 /loupan/{ID}/chengjiao/i3{N}/ 或
    等价形式，真实 dump 为 /house-xm2810210234/i32/ 对应第 2 页），
    只收录包含同一 ID 的链接并 urljoin 还原绝对地址，避免误采全市/商圈
    翻页；无分页链接则返回空列表（单页小区）。

    分页 URL 的页号由链接文本（页码数字）决定，不依赖 URL 内数字猜测。
    """
    token = _id_token_from_url(page_url)
    if token is None:
        return []
    page_urls: list[tuple[int, str]] = []
    seen_pages: set[int] = set()
    # 匹配 <a href="...">2</a> 或 <a href="...">下一页</a>；页号只取纯数字文本
    for match in re.finditer(
        r'<a[^>]*href="([^"]+)"[^>]*>\s*(\d+)\s*</a>',
        first_page_html or "",
    ):
        href = urljoin(page_url, unescape(match.group(1)))
        path = urlparse(href).path
        page_no = int(match.group(2))
        if token not in path:
            continue
        if page_no in seen_pages or page_no < 2 or page_no > MAX_PAGES:
            continue
        seen_pages.add(page_no)
        page_urls.append((page_no, href))
    return [href for _, href in sorted(page_urls)]


# ============================================================
# 在售采集（URL 直达挂牌聚合页）
# ============================================================

def print_listing_snapshots(snapshots: list):
    if not snapshots:
        print("房天下: 未抓到在售房源摘要")
        return
    for item in snapshots:
        print(
            "房天下: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )
        _url = item.listing_url or "-"
        print("  房源链接:", _url if len(_url) <= 72 else _url[:69] + "...")


async def collect_listing(
    tab,
    *,
    target: dict,
    area: Optional[float],
    debug: bool,
) -> tuple[list[ListingSnapshot], str]:
    """直达挂牌聚合页采集在售快照（含 URL 直达翻页），返回（快照, 落地页 HTML）。

    走工程风控协议（命中拦截暂停等人工，干净后才解析）；空态短路；
    页面归属校验：快照与目标小区不匹配则整页弃用（防入口串页）。
    """
    listing_url = target["listing_page_url"]
    await tab.get(listing_url)
    await tab
    await asyncio.sleep(3)

    html = await wait_and_reload_after_block(
        tab, _platform.detect_block, f"小区挂牌页[{target['community_name']}]"
    )
    if debug:
        await dump_html(tab, f"fang_listing_{target['community_id'] or 'direct'}_p1")

    # 空态识别：无在售卡即短路，跳过在售解析与翻页（下方可能是推荐位）
    if _platform.is_no_result(html):
        log.info("[空态] %s 在房天下在售 0 条（跳过推荐位）", target["community_name"])
        return [], html

    area_confirmed = True
    if area is not None:
        area_confirmed = await fill_area_inputs(tab, area, area)
        if not area_confirmed:
            log.warning("[筛选] 面积筛选未成功提交，本次为全量在售")
        html = await tab.get_content()

    snapshots = _platform.parse_listing_snapshots(html, base_url=tab.target.url)

    # 页面归属校验（工程件，与其余 MVP 模板同款）：快照须与目标小区名
    # 匹配，否则判定入口落地页错误，整页弃用且不翻页（防串页/推荐位混入）。
    # 仅库内入口（community_id 有值）校验；直传 URL（community_id=None）是
    # 人工核对过的调试入口，跳过归属（否则"直传URL"占位名永不匹配会误弃数据）
    if (
        target.get("community_id") is not None
        and snapshots
        and not has_matching_community_snapshots(snapshots, target["community_name"])
    ):
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

    # URL 直达翻页：仅归属校验通过才翻页；空页 = 已翻过真实末页，停止
    page_urls = build_page_urls(listing_url, html) if snapshots else []
    if page_urls:
        log.info("[翻页] 在售第 1 页 %d 条；另有 %d 页待采", len(snapshots), len(page_urls))
    for page_no, page_url in enumerate(page_urls, start=2):
        await tab.get(page_url)
        await tab
        await asyncio.sleep(2)
        page_html = await wait_and_reload_after_block(
            tab, _platform.detect_block, f"在售翻页第 {page_no} 页"
        )
        if debug:
            await dump_html(tab, f"fang_listing_{target['community_id'] or 'direct'}_p{page_no}")
        page_snapshots = _platform.parse_listing_snapshots(page_html, base_url=tab.target.url)
        snapshots.extend(page_snapshots)
        if not page_snapshots:
            log.info("[翻页] 在售第 %d 页无房源，停止翻页", page_no)
            break
        log.info("[翻页] 在售第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_snapshots), len(snapshots))

    return snapshots, html


# ============================================================
# 成交采集（URL 直达成交页，真实成交全量）
# ============================================================

def print_deal_records(deals: list[dict]):
    if not deals:
        print("房天下: 未抓到成交记录")
        return
    print("房天下成交明细（真实成交，全量）:")
    for item in deals:
        print(
            f"  {item.get('area') or '-'}㎡ | {item.get('date') or '-'} | "
            f"{item.get('total_price') or '-'}万 | {item.get('price') or '-'}元/㎡"
        )


async def click_page_number(page, page_no: int) -> Optional[str]:
    """回退方案：点击房天下页码（文本定位），返回加载完成后的 HTML。

    DOM: <div class="page_box"><div class="page_al"><span><a href="...">2</a></span>
    """
    try:
        elements = await page.select_all("div.page_box a", timeout=3)
    except Exception:
        elements = []
    target = None
    for el in elements:
        try:
            text = await el.apply("(el) => el.textContent.trim()")
        except Exception:
            text = ""
        if text == str(page_no):
            target = el
            break
    if target is None:
        return None
    if not await human_click(page, target, f"page {page_no}"):
        return None
    await page
    await asyncio.sleep(3)
    return await page.get_content()


async def collect_deals(
    tab,
    *,
    target: dict,
    debug: bool,
) -> list[dict]:
    """直达成交页采集真实成交明细（全量，不做面积/近半年裁剪）。

    成交记录入库键名与工程入库层对齐：{area, date, total_price(万), price(元/㎡)}。
    成交分页优先 URL 直达（原生分页链接收集）；页面无分页链接但总页数
    声明大于 1 时回退点击（老 MVP 验证过的写法）。
    """
    deal_url = target.get("deal_page_url")
    if not deal_url:
        log.info("[成交] %s 无房天下成交入口（deal_page_url 缺失），跳过成交", target["community_name"])
        return []

    await tab.get(deal_url)
    await tab
    await asyncio.sleep(3)
    first_html = await wait_and_reload_after_block(
        tab, _platform.detect_block, f"小区成交页[{target['community_name']}]"
    )
    if debug:
        await dump_html(tab, f"fang_deal_{target['community_id'] or 'direct'}_p1")

    raw_records = _platform.parse_deal_records(first_html)
    deals = [
        {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
        for r in raw_records
        # 缺面积或单价视为脏行，不进入成交事实
        if r[0] is not None and r[3] is not None
    ]
    log.info("[成交] %s 第 1 页真实成交 %d 条", target["community_name"], len(deals))

    # 成交分页：优先 URL 直达
    page_urls = build_page_urls(deal_url, first_html)
    total_pages = parsers.parse_total_pages(first_html)

    if page_urls:
        log.info("[成交] 另有 %d 页待采（URL 直达）", len(page_urls))
        for page_no, page_url in enumerate(page_urls, start=2):
            await tab.get(page_url)
            await tab
            await asyncio.sleep(2)
            page_html = await wait_and_reload_after_block(
                tab, _platform.detect_block, f"成交翻页第 {page_no} 页"
            )
            if debug:
                await dump_html(tab, f"fang_deal_{target['community_id'] or 'direct'}_p{page_no}")
            page_raw = _platform.parse_deal_records(page_html)
            page_deals = [
                {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
                for r in page_raw if r[0] is not None and r[3] is not None
            ]
            deals.extend(page_deals)
            if not page_deals:
                log.info("[成交] 第 %d 页无成交记录，停止翻页", page_no)
                break
            log.info("[成交] 第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_deals), len(deals))
    elif total_pages > 1:
        # 页面无原生分页链接但声明多页：回退点击（老 MVP 写法兜底）
        log.info("[成交] 页面无 URL 分页链接（共 %d 页），回退点击翻页", total_pages)
        for page_no in range(2, total_pages + 1):
            page_html = await click_page_number(tab, page_no)
            if page_html is None:
                log.warning("[成交] 第 %d 页无法翻页，停止", page_no)
                break
            page_html = await wait_and_reload_after_block(
                tab, _platform.detect_block, f"成交点击第 {page_no} 页"
            )
            if debug:
                await dump_html(tab, f"fang_deal_{target['community_id'] or 'direct'}_p{page_no}")
            page_raw = _platform.parse_deal_records(page_html)
            page_deals = [
                {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
                for r in page_raw if r[0] is not None and r[3] is not None
            ]
            deals.extend(page_deals)
            if not page_deals:
                break

    log.info(
        "[成交汇总] %s：真实成交 %d 条",
        target["community_name"],
        len(deals),
    )
    return deals


# ============================================================
# 单小区直达采集（在售 + 成交）
# ============================================================

async def collect_community(
    tab,
    *,
    target: dict,
    area: Optional[float],
    debug: bool,
) -> CommunityCollection:
    """单小区直达采集：在售（挂牌聚合页）+ 真实成交（成交页）。

    房天下成交是真实成交事实，无论挂牌是否空态都独立采集（成交页与挂牌
    页是不同 URL 的独立入口，空态小区仍可能有历史成交）。返回原始
    CommunityCollection；筛选/算法在编排层。
    """
    listing_url = target["listing_page_url"]
    deal_url = target.get("deal_page_url")

    listing_snapshots, listing_html = await collect_listing(
        tab, target=target, area=area, debug=debug
    )
    listing_empty = _platform.is_no_result(listing_html)

    # 成交采集：入口存在即独立执行（挂牌空态不影响成交事实采集）
    deals: list[dict] = []
    if deal_url:
        deals = await collect_deals(tab, target=target, debug=debug)
        print_deal_records(deals)

    print_listing_snapshots(listing_snapshots)

    has_listing = bool(listing_snapshots)
    has_deals = bool(deals)
    if not has_listing and not has_deals:
        status = PlatformResultStatus.NO_DATA
        log.info(
            "[采集汇总] %s：在售 0 条、成交 0 条（%s）",
            target["community_name"],
            "挂牌空态" if listing_empty else "无数据",
        )
    else:
        status = PlatformResultStatus.SUCCESS
        log.info(
            "[采集汇总] %s：在售 %d 条，真实成交 %d 条",
            target["community_name"],
            len(listing_snapshots),
            len(deals),
        )

    return CommunityCollection(
        platform="fang",
        community_id=target["community_id"],
        community_name=target["community_name"],
        listing_page_url=listing_url,
        deal_page_url=deal_url,
        status=status,
        blocked_reason=None,  # 风控协议：拦截在页内暂停等人工，返回即干净
        listings=listing_snapshots,
        deals=deals,  # 真实成交明细（仅 lj/fang 有）
        community_avg_price=None,  # 房天下不采小区均价
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
        print("[错误] 没有可采集目标：--community-id 未命中房天下入口，且未传 --listing-url")
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

    # 先开房天下首页建立会话：指令必带 manual_login，首页先人工过
    # 验证码/登录，回车确认；再由工程风控件复检，干净后逐个直达小区
    tab = await browser.get(START_URL)
    await tab
    await asyncio.sleep(3)
    if manual_login:
        await wait_for_manual_login()
    await wait_and_reload_after_block(tab, _platform.detect_block, "房天下首页")

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

        # 采集完成：浏览器保持打开，等人工回车确认结束
        # （回车 / Ctrl+C 均会关闭浏览器窗口；期间人工关闭浏览器也会直接结束）。
        # 必须用单个 daemon 线程等回车：wait_for+to_thread 每次超时会留下
        # 堵在 stdin 的孤儿线程（2026-09-03 ke 实测教训），这种写法不可用
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
            await dump_html(tab, "fang_error")
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
        description="房天下在售+真实成交直达采集 MVP（URL 直达，无搜索/登录；成交为真实明细全量）"
    )
    parser.add_argument(
        "--community-id",
        type=int,
        nargs="+",
        default=[],
        help="小区 ID（按 community_platform_pages 的 fang 入口查找挂牌+成交），可多个",
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
