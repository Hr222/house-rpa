# -*- coding: utf-8 -*-
"""贝壳 adapter（nodriver 版）：模拟真人操作执行采集。

严格按 15 步流程：
  4.收到请求 → 5.刷新保活 → 6.点更多选项 → 7.选面积档位
  → 8.输入小区名搜索 → 9.抓在售单价 → 10.点小区详情新tab
  → 11.抓成交单价 → 12.筛选求均值 → 13.算最终价 → 14.返回
  → 15.详情tab定时关闭(后台)

模拟真人：用 JS 点击/CDP 输入，不直接拼 URL。
"""
import asyncio
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Optional, List

from nodriver import cdp

import config
from app import parsers
from app.models import PlatformResult, Listing, DealRecord

log = logging.getLogger(__name__)

# debug 落盘目录
DEBUG_DIR = Path(__file__).parent.parent / "debug"


async def _dump_html(page, name: str):
    """把当前页面渲染后的 HTML 存到 debug/ 目录，方便排查。"""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        html = await _evaluate(page, "document.documentElement.outerHTML")
        ts = time.strftime("%H%M%S")
        out = DEBUG_DIR / f"{ts}_{name}.html"
        out.write_text(str(html), encoding="utf-8")
        log.info("  [debug] %s → %s (%d字符)", name, out.name, len(str(html)))
    except Exception as e:
        log.warning("  [debug] 落盘失败 %s: %s", name, e)

# 贝壳面积档位：(上限㎡, URL段)
# a1=50以下 a2=50-70 a3=70-90 a4=90-110 a5=110-140 a6=140-170 a7=170以上
AREA_SEGMENTS = [
    (50, "a1"), (70, "a2"), (90, "a3"), (110, "a4"),
    (140, "a5"), (170, "a6"), (9999, "a7"),
]


def _pick_segments(area_min: float, area_max: float) -> List[str]:
    """请求区间[min,max] → 覆盖的贝壳档位列表（跨档位分别搜再合并）。

    例：72~108 → a3(70-90) + a4(90-110)
    """
    segs = []
    for upper, seg in AREA_SEGMENTS:
        # 档位区间：(prev_upper, upper)
        prev = AREA_SEGMENTS[[s for _, s in AREA_SEGMENTS].index(seg) - 1][0] \
            if seg != "a1" else 0
        # 请求区间和档位区间有交集就选
        if area_min < upper and area_max > prev:
            segs.append(seg)
    return segs


async def _evaluate(page, js: str):
    """执行 JS 并取真实值（处理 nodriver 的 RemoteObject 包装）。"""
    result = await page.evaluate(js, return_by_value=True)
    if hasattr(result, 'deep_serialized_value') and result.deep_serialized_value:
        return result.deep_serialized_value.value
    if hasattr(result, 'value') and result.value is not None:
        return result.value
    return result


async def _refresh(page):
    """第5步：刷新界面（保活插口，后面接定时保活逻辑）。"""
    log.info("[5] 刷新界面（保活）")
    await page.evaluate("location.reload()", return_by_value=True)
    await asyncio.sleep(3)


async def _click_more_options(page):
    """第6步：点击"更多选项"展开。"""
    log.info("[6] 点击更多选项展开")
    await page.evaluate("""
        (() => {
            const btn = document.querySelector('.btn-showmore')
                    || document.querySelector('[class*="showmore"]');
            if (btn) { btn.click(); return 'ok'; }
            // 兜底：找文字
            const all = document.querySelectorAll('span, div, a');
            for (const el of all) {
                if ((el.innerText||'').trim() === '更多' && el.className.includes('showmore')) {
                    el.click(); return 'ok-text';
                }
            }
            return 'not found';
        })()
    """, return_by_value=True)
    await asyncio.sleep(1.5)


async def _get_html(page) -> str:
    return await _evaluate(page, "document.documentElement.outerHTML")


async def collect(browser, main_page, community_name: str,
                  area_min: float, area_max: float) -> PlatformResult:
    """执行贝壳采集（步骤5~13）。main_page 是常驻的二手房页。

    browser: nodriver.Browser
    main_page: 常驻的 /ershoufang/ 页 tab
    """
    start = time.time()
    try:
        return await _do_collect(browser, main_page, community_name, area_min, area_max)
    except Exception as e:
        log.exception("贝壳采集异常")
        return PlatformResult(name="贝壳", status="ERROR", reason=str(e))
    finally:
        log.info("贝壳采集耗时 %.1fs", time.time() - start)


async def _do_collect(browser, main_page, community_name: str,
                      area_min: float, area_max: float) -> PlatformResult:
    # 第5步：刷新保活
    await _refresh(main_page)
    await _dump_html(main_page, "05_refresh")

    # 第6步：点更多选项
    await _click_more_options(main_page)
    await _dump_html(main_page, "06_more_options")

    # 第7步：选面积档位 + 第8步：输入小区名搜索
    # 先搜小区名，拿到小区ID，再按档位搜
    segs = _pick_segments(area_min, area_max)
    log.info("[7] 面积区间 %.0f~%.0f㎡ → 档位 %s", area_min, area_max, segs)

    all_unit_prices = []
    detail_url = None

    for seg in segs:
        # 第8步：输入小区名 + 选档位 + 搜索（模拟真人）
        prices, durl = await _search_one_segment(
            main_page, community_name, seg)
        all_unit_prices.extend(prices)
        if durl:
            detail_url = durl
        if len(segs) > 1:
            await asyncio.sleep(3)  # 多档位之间间隔，降风控

    # 第9步结果：在售房源单价列表
    log.info("[9] 在售单价共 %d 条", len(all_unit_prices))

    # 第10~12步：详情页
    community_avg = None
    deal_prices = []
    if detail_url:
        community_avg, deal_prices = await _fetch_detail(
            browser, detail_url, area_min, area_max)

    listings = [Listing(unit_price=p) for p in all_unit_prices if p]
    deals = [DealRecord(unit_price=p) for p in deal_prices if p]

    status = "SUCCESS" if (listings or deals or community_avg) else "NO_DATA"
    return PlatformResult(
        name="贝壳", status=status,
        community_avg_price=community_avg,
        listings=listings, deals=deals,
    )


async def _search_one_segment(page, community_name: str, seg: str):
    """第8步：在一个档位上搜索小区，返回 (单价列表, 详情链接)。"""
    log.info("[8] 搜索: 小区=%s 档位=%s", community_name, seg)

    # 清空 + 输入小区名（CDP 真实输入）
    await page.evaluate("""
        (() => {
            const inp = document.querySelector('#searchInput');
            if (inp) { inp.value = ''; inp.focus(); }
        })()
    """, return_by_value=True)
    await asyncio.sleep(0.3)
    await page.send(cdp.input_.insert_text(text=community_name))
    await asyncio.sleep(1.5)

    # 回车搜索（CDP 真实按键）
    await page.send(cdp.input_.dispatch_key_event(
        type_="rawKeyDown", key="Enter", code="Enter",
        windows_virtual_key_code=13, native_virtual_key_code=13))
    await page.send(cdp.input_.dispatch_key_event(
        type_="keyUp", key="Enter", code="Enter",
        windows_virtual_key_code=13, native_virtual_key_code=13))
    await asyncio.sleep(4)

    # 搜索结果URL里加档位（点档位筛选）
    cur_url = page.target.url
    log.info("  搜索后URL: %s", cur_url)
    await _dump_html(page, f"08_search_{seg}")

    # 从搜索结果提取小区ID，拼档位URL重新访问
    html = await _get_html(page)
    detail_url = parsers.find_detail_link(html)

    # 提取小区ID
    xiaoqu_id = None
    if detail_url:
        import re
        m = re.search(r'/xiaoqu/(\d+)', detail_url)
        if m:
            xiaoqu_id = m.group(1)

    if xiaoqu_id:
        # 按档位 + 小区ID 访问
        seg_url = f"https://sz.ke.com/ershoufang/{seg}c{xiaoqu_id}/"
        log.info("  档位筛选: %s", seg_url)
        await page.goto(seg_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        html = await _get_html(page)
        if not detail_url:
            detail_url = parsers.find_detail_link(html)

    # 第9步：抓单价
    raw = parsers.parse_listings(html)
    prices = [r["unit_price"] for r in raw if r.get("unit_price")]
    log.info("  档位%s: 单价 %d 条", seg, len(prices))
    return prices, detail_url


async def _fetch_detail(browser, detail_url: str,
                        area_min: float, area_max: float):
    """第10~12步：新tab抓详情页。返回 (小区均价, 成交单价列表)。"""
    log.info("[10] 打开详情页: %s", detail_url)
    detail_tab = await browser.get(detail_url, new_tab=True)
    try:
        await asyncio.sleep(3)
        html = await _get_html(detail_tab)

        # 第11步：小区均价 + 成交记录单价
        community_avg = parsers.parse_community_avg_price(html)
        raw_deals = parsers.parse_deals(html)
        deal_prices = [d["unit_price"] for d in raw_deals if d.get("unit_price")]
        log.info("[11] 小区均价=%s, 成交单价=%d条", community_avg, len(deal_prices))
        await _dump_html(detail_tab, "11_detail")

        return community_avg, deal_prices
    finally:
        # 第15步：详情tab定时停留后关闭（后台，不阻塞）
        asyncio.create_task(_close_tab_after_linger(detail_tab))


async def _close_tab_after_linger(tab):
    """第15步：详情页停留60s后关闭（后台定时，不阻塞返回）。"""
    try:
        log.info("[15] 详情页停留 %ds 后关闭（后台）",
                 config.DETAIL_TAB_LINGER_SECONDS)
        await asyncio.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        await tab.close()
        log.info("[15] 详情页已关闭")
    except Exception as e:
        log.warning("详情页关闭异常: %s", e)
