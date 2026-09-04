# -*- coding: utf-8 -*-
"""安居客 MVP 测试脚本（URL 直达抓取形式）。

抓取形式（2026-09-02 改造）：小区挂牌入口已由统一初始化工具
（scripts/initialize_community_page/）存入 community_platform_pages，
本脚本按 community_id 直达挂牌列表页，不再走
"首页 → 搜索 → 面积筛选"的模拟点击链路。

流程：读取库内 ajk 入口 → page.get(listing_page_url) 直达 →
风控检测（协议不变，拦截只报告不自动重试）→（可选 --area）面积筛选 →
直接读取 HTML 解析标签（在售快照 + 挂牌均价，无滚动/点击）。
浏览器全程存活，抓完不主动关闭。

安居客无成交记录，挂牌均价仅作展示；成交采集仅 lj/fang，另行处理。

用法：
  python -m scripts.collect_community_data.rpa.ajk_mvp_test --community-id 123 456
  python -m scripts.collect_community_data.rpa.ajk_mvp_test --listing-url "https://shenzhen.anjuke.com/sale/?comm_id=xxx" --area 89.5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import nodriver as uc
from nodriver.core import util as nodriver_util

from app.rpa.core import config
from app.rpa.core.models import ListingSnapshot
from app.rpa.core.status import PlatformResultStatus
from app.rpa.platforms.adapters import ajk as ajk_adapter
from app.rpa.platforms.base import wait_and_reload_after_block, has_matching_community_snapshots
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging
from scripts.collect_community_data.models import CommunityCollection


setup_logging()
log = logging.getLogger("ajk-mvp-test")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PAGES_DB = PROJECT_ROOT / "persist" / "property_records.sqlite3"

# 安居客深圳二手房首页：先开首页建立会话/Cookie，再逐个直达小区列表页
START_URL = "https://shenzhen.anjuke.com/sale/"

# 翻页防失控兜底：单小区最大翻页数（每页 60 条，50 页 = 3000 条上限）
MAX_PAGES = 50

# 常驻浏览器引用：批次结束保持存活（atexit 摘除，脚本退出不关闭）；
# 终端 Ctrl+C 终止时由 terminate_browser() 同步关闭——事件循环被冻结，
# MVP 协程的 finally 无法自行收尾，必须在进程退出层处理
_browser = None


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


async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


async def browser_gone(tab) -> bool:
    """人工关闭浏览器（窗口/进程）后连接断开，探测返回 True。"""
    try:
        await tab.get_content()
        return False
    except Exception:
        return True


# ============================================================
# 通用：人工等待
# ============================================================

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
    """定位面积筛选区的自定义输入框并填值。

    安居客结果页里有两处 input.input：一处在价格筛选区（unit=万），
    一处在面积筛选区（unit=㎡）。靠父级 li 内的 unit 文本是"㎡"区分。
    填值后"确定"按钮会显示，需点击提交。

    注意：Element 用 query_selector_all（不是 select_all，那是 Tab 的方法）。
    """
    # 1. 遍历所有 line-item-input，靠 unit 文本是"㎡"定位面积区
    line_items = await page.select_all("li.line-item-input")
    log.info("[3] line-item-input 数量: %d", len(line_items))

    area_li = None
    for li in line_items:
        unit_text = await li.apply(
            "(el) => { const u = el.querySelector('.unit'); return u ? u.textContent.trim() : ''; }"
        )
        log.info("[3]   li unit 文本: %r", unit_text)
        if unit_text == "㎡":
            area_li = li
            break

    if area_li is None:
        raise RuntimeError("未找到面积筛选区（unit=㎡ 的 line-item-input）")

    # 2. 取该 li 下两个 input
    inputs = await area_li.query_selector_all("input.input")
    log.info("[3] 面积区 input 数量: %d", len(inputs))
    if len(inputs) < 2:
        raise RuntimeError(f"面积区 input 不足 2 个，实际 {len(inputs)} 个")
    min_el, max_el = inputs[0], inputs[1]

    # 3. 填下限
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

    # 4. 填上限
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

    # 5. 点"确定"提交
    confirms = await area_li.query_selector_all(".confirm")
    log.info("[3] 确定按钮数量: %d", len(confirms))
    confirm_clicked = False
    if confirms:
        confirm_clicked = await human_click(page, confirms[0], "area confirm")
    if not confirm_clicked:
        # 兜底回车
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
# 解析：主结果区在售快照（截断到"推荐以下房源"之前）与挂牌均价
# ============================================================

def parse_main_listing_prices(html: str) -> list:
    """提取主结果区在售单价（兼容旧调用）。"""
    return [s.unit_price for s in parse_listing_snapshots(html) if s.unit_price]


def _extract_first(pattern, text, cast=float):
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return cast(m.group(1).replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_listing_snapshots(html: str) -> list:
    """提取主结果区房源快照。

    安居客结果页结构：主结果区与推荐区是两个并列的 <section class="list">，
    中间靠 <h3 class="list-guess-title">分隔。只取边界标志之前的部分。

    单条房源字段：
      - 户型: property-content-info-attribute（如 3室2厅2卫）
      - 面积: property-content-info-text 里的 XX.XX㎡
      - 小区名: property-content-info-comm-name
      - 总价: property-price-total-num
      - 单价: property-price-average
    """
    cut = html.find("list-guess-title")
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(
        r'<div[^>]*class="property"[^>]*>(.*?)(?=<div[^>]*class="property"|$)',
        main_html,
        re.S,
    ):
        chunk = block.group(1)

        # 户型: <p class="...attribute"><span>3</span>室<span>2</span>厅<span>2</span>卫
        layout = None
        attr_m = re.search(
            r'property-content-info-attribute[^>]*>(.*?)</p>', chunk, re.S
        )
        if attr_m:
            nums = re.findall(r'<span[^>]*>(\d+)</span>', attr_m.group(1))
            labels = re.findall(r'(室|厅|卫)', attr_m.group(1))
            if len(nums) >= 2:
                layout = f"{nums[0]}室{nums[1]}厅"

        # 面积: property-content-info-text 里的 XX.XX㎡
        area = _extract_first(r'([\d.]+)\s*㎡', chunk)

        # 小区名
        name_m = re.search(
            r'property-content-info-comm-name[^>]*>([^<]+)<', chunk
        )
        community_name = name_m.group(1).strip() if name_m else None

        # 总价(万)
        total_price = _extract_first(
            r'property-price-total-num[^>]*>\s*([\d,]+)', chunk
        )

        # 单价
        unit_price = _extract_first(
            r'property-price-average[^>]*>\s*([\d,]+)\s*元', chunk
        )

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


def print_listing_snapshots(snapshots: list):
    """对齐 batch_mvp_test.print_platform_details 的打印格式。"""
    if not snapshots:
        print("安居客: 未抓到房源摘要")
        return
    for item in snapshots:
        print(
            "安居客: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )


def parse_community_avg_price(html: str):
    """从结果页顶部社区卡片提取挂牌均价。

    安居客结果页顶部社区信息卡：
      <div class="community-info-detail-price">
        <p class="community-info-detail-price-money"><em>84307</em>元/㎡</p>
      </div>

    注意：安居客无成交记录，业务上把挂牌均价当作 deal_prices 的替代，
    加权落点中位数算法只使用在售房源价格；这组替代成交数据仅保留用于采集记录。
    """
    m = re.search(
        r'community-info-detail-price-money[^>]*>\s*<em[^>]*>\s*([\d,]+)\s*</em>\s*元\s*/?\s*㎡',
        html,
    )
    return float(m.group(1).replace(",", "")) if m else None


# ============================================================
# 库内入口查找：只抓"有的"小区
# ============================================================

def resolve_listing_urls(community_ids: list[int]) -> list[dict]:
    """从房源记录库读取安居客已初始化的挂牌入口。

    白名单语义：community_platform_pages 里没有 ajk 入口的小区直接跳过。
    """
    con = sqlite3.connect(f"file:{PAGES_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    targets: list[dict] = []
    for community_id in community_ids:
        row = con.execute(
            "SELECT community_id, source_community_name, listing_page_url "
            "FROM community_platform_pages WHERE community_id = ? AND source_platform = 'ajk'",
            (community_id,),
        ).fetchone()
        if row is None or not row["listing_page_url"]:
            log.warning("[跳过] 小区 %s 无安居客挂牌入口（未初始化）", community_id)
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


# ============================================================
# 单小区直达采集
# ============================================================

def build_page_urls(listing_url: str, first_page_html: str) -> list[str]:
    """由第 1 页分页链接推导后续页 URL（/sale/p{N}/?comm_id=，URL 直达无点击）。

    页码取自第 1 页 HTML 中指向同一小区（comm_id 一致）的分页链接；
    只保留第 2 页起，并套 MAX_PAGES 兜底。无 comm_id 或无分页链接
    则返回空列表（单页小区）。
    """
    parsed = urlparse(listing_url)
    origin = f"{parsed.scheme}://{parsed.hostname}"
    comm_id_m = re.search(r"comm_id=(\d+)", listing_url)
    if comm_id_m is None:
        return []
    comm_id = comm_id_m.group(1)
    page_nums = sorted(
        {
            int(number)
            for number in re.findall(rf"/sale/p(\d+)/\?comm_id={comm_id}\b", first_page_html or "")
        }
    )
    return [
        f"{origin}/sale/p{page_no}/?comm_id={comm_id}"
        for page_no in page_nums
        if 2 <= page_no <= MAX_PAGES
    ]


async def collect_community(
    tab,
    *,
    target: dict,
    area: Optional[float],
    debug: bool,
) -> CommunityCollection:
    """单小区直达采集：导航挂牌页 → 风控协议 →（可选）面积筛选 →
    读 HTML 解析原始快照与挂牌均价（含 URL 直达翻页）。

    返回原始 CommunityCollection（一个对象，两个 list）；筛选计算
    在编排层。风控为工程 RPA 件 wait_and_reload_after_block +
    adapter.detect_block：命中拦截 → 置前 + 暂停等人工 → 回车 →
    复检，直到干净才返回（协议不可改动，此处零新造）。
    """
    listing_url = target["listing_page_url"]
    await tab.get(listing_url)
    await tab
    await asyncio.sleep(3)

    # 第 1 页：走工程风控协议，风控未解除前不会返回
    html = await wait_and_reload_after_block(
        tab, ajk_adapter.detect_block, f"小区挂牌页[{target['community_name']}]"
    )
    if debug:
        await dump_html(tab, f"ajk_listing_{target['community_id'] or 'direct'}_p1")

    # 空态识别：调用子平台 adapter 导出的判空能力（与 detect_block 同级，
    # 洪湖14号大院类，真实 dump 已核对，见 ajk_adapter.is_no_result）。
    # 在售 0 套，小区均价卡照常展示，照常采集
    if ajk_adapter.is_no_result(html):
        community_avg_price = parse_community_avg_price(html)
        log.info(
            "[空态] %s 在安居客在售 0 条（页面为无房源空态），挂牌均价 %s 元/㎡",
            target["community_name"],
            f"{community_avg_price:.0f}" if community_avg_price else "未识别",
        )
        return CommunityCollection(
            platform="ajk",
            community_id=target["community_id"],
            community_name=target["community_name"],
            listing_page_url=listing_url,
            deal_page_url=None,  # 安居客无成交页
            status=PlatformResultStatus.NO_DATA,
            blocked_reason=None,
            listings=[],
            deals=[],  # 安居客无成交记录
            community_avg_price=community_avg_price,
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

    # 页面归属校验（工程件，与 lyj 模板同款）：快照须与目标小区名匹配，
    # 不符则整页弃用且不翻页（防入口串页/推荐位混入）
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
            tab, ajk_adapter.detect_block, f"翻页第 {page_no} 页"
        )
        if debug:
            await dump_html(tab, f"ajk_listing_{target['community_id'] or 'direct'}_p{page_no}")
        page_snapshots = parse_listing_snapshots(page_html)
        snapshots.extend(page_snapshots)
        if not page_snapshots:
            # 空页 = 已翻过真实在售末页，停止翻页
            log.info("[翻页] 第 %d 页无在售，停止翻页", page_no)
            break
        log.info("[翻页] 第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_snapshots), len(snapshots))

    log.info(
        "[采集汇总] %s：在售 %d 条，挂牌均价 %s 元/㎡",
        target["community_name"],
        len(snapshots),
        f"{community_avg_price:.0f}" if community_avg_price else "未识别",
    )

    return CommunityCollection(
        platform="ajk",
        community_id=target["community_id"],
        community_name=target["community_name"],
        listing_page_url=listing_url,
        deal_page_url=None,  # 安居客无成交页
        status=PlatformResultStatus.SUCCESS if snapshots else PlatformResultStatus.NO_DATA,
        blocked_reason=None,  # 风控协议：拦截在页内暂停等人工，返回即干净
        listings=snapshots,
        deals=[],  # 安居客无成交记录
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
    筛选计算在编排层）。采集结束沿用旧 MVP 收尾：浏览器保持
    打开等人工回车确认；人工关闭浏览器则脚本直接结束（浏览器
    本身不关闭）。
    """
    if debug:
        set_debug_mode(True)

    global _browser
    targets: list[dict] = []
    if listing_url:
        targets.append(
            {"community_id": None, "community_name": "直传URL", "listing_page_url": listing_url}
        )
    targets.extend(resolve_listing_urls(community_ids))
    if not targets:
        print("[错误] 没有可采集目标：--community-id 未命中安居客入口，且未传 --listing-url")
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
    _browser = browser
    # 浏览器常驻：把实例从 nodriver 的 atexit 清理注册表移除，
    # 批次结束/脚本退出都不关闭浏览器（对齐工程 runtime 的常驻模式）
    nodriver_util.__registered__instances__.discard(browser)

    # 先开安居客首页建立会话：指令必带 manual_login，首页先人工过
    # 验证码/登录，回车确认；再由工程风控件复检，干净后逐个直达小区
    tab = await browser.get(START_URL)
    await tab
    await asyncio.sleep(3)
    if manual_login:
        await wait_for_manual_login()
    await wait_and_reload_after_block(tab, ajk_adapter.detect_block, "安居客首页")

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
            await dump_html(tab, "ajk_error")
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
    parser = argparse.ArgumentParser(description="安居客在售直达采集 MVP（URL 直达，无搜索/登录）")
    parser.add_argument(
        "--community-id",
        type=int,
        nargs="+",
        default=[],
        help="小区 ID（按 community_platform_pages 的 ajk 入口查找），可多个",
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
    try:
        uc.loop().run_until_complete(
            main(
                community_ids=args.community_id,
                listing_url=args.listing_url,
                area=args.area,
                manual_login=args.manual_login,
                debug=args.debug,
            )
        )
    except KeyboardInterrupt:
        # 终端 Ctrl+C：事件循环已冻结，MVP 协程的 finally 不会执行，
        # 在进程退出层同步终止常驻浏览器
        terminate_browser()


if __name__ == "__main__":
    cli()
