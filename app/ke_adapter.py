# -*- coding: utf-8 -*-
"""贝壳 adapter（nodriver 官方用法）。

严格模拟真人：所有交互用 nodriver Element API（select/find/click/send_keys）。
不手搓 CDP 事件，不用 JS .click()。

15 步流程：
  1.启动浏览器 → 2.用户登录 → 3.停在二手房页等请求
  4.收到请求{小区名,面积区间} → 5.刷新保活(插口) → 6.点更多选项
  7.匹配面积档位(跨档位分别搜) → 8.输入小区名搜索
  9.抓在售单价 → 10.点查看小区详情(新tab) → 11.抓小区均价+成交单价
  12.成交按面积筛选求均值 → 13.算最终单价 → 14.返回结果
  15.详情tab后台停留60s关闭(不阻塞)
"""
import asyncio
import logging
import random
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

import config
from app import parsers
from app.models import PlatformResult

log = logging.getLogger(__name__)

DEBUG_DIR = Path(__file__).parent.parent / "debug"


# ===== 工具函数 =====

async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    """真人操作间隔。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _dump(page, name: str):
    """渲染后 HTML 落盘到 debug/。"""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        content = await page.get_content()
        ts = time.strftime("%H%M%S")
        out = DEBUG_DIR / f"{ts}_{name}.html"
        out.write_text(content, encoding="utf-8")
        log.info("  [debug] %s → %s", name, out.name)
    except Exception as e:
        log.warning("  [debug] 落盘失败 %s: %s", name, e)


def _pick_segments(area_min: float, area_max: float) -> List[str]:
    """请求区间[min,max] → 覆盖的贝壳档位列表（跨档位分别搜再合并）。"""
    bounds = [(0, 50, "a1"), (50, 70, "a2"), (70, 90, "a3"), (90, 110, "a4"),
              (110, 140, "a5"), (140, 170, "a6"), (170, 99999, "a7")]
    segs = []
    for lo, hi, seg in bounds:
        if area_min < hi and area_max > lo:
            segs.append(seg)
    return segs


# ===== 采集主流程（步骤5~13）=====

async def collect(browser, main_page, community_name: str,
                  area_min: float, area_max: float) -> PlatformResult:
    start = time.time()
    log.info("[4] 收到请求: 小区=%s 面积=%.0f~%.0f㎡", community_name, area_min, area_max)
    try:
        return await _do_collect(browser, main_page, community_name, area_min, area_max)
    except Exception as e:
        log.exception("采集异常")
        return PlatformResult(name="贝壳", status="ERROR", reason=str(e))
    finally:
        log.info("采集耗时 %.1fs", time.time() - start)


async def _do_collect(browser, main_page, community_name: str,
                      area_min: float, area_max: float) -> PlatformResult:
    # 第5步：刷新保活（插口）
    log.info("[5] 刷新页面（保活插口）")
    await main_page.reload()
    await main_page.select("#searchInput", timeout=15)  # 等页面加载完
    await main_page  # 等事件稳定
    await _delay()
    await _dump(main_page, "05_refresh")

    # 第6步：点更多选项（nodriver select + click）
    log.info("[6] 点击更多选项")
    try:
        more_btn = await main_page.select(".more.btn-more", timeout=3)
        if more_btn:
            await more_btn.click()
            await main_page
            await _delay()
    except Exception:
        log.info("  更多选项可能已展开，跳过")
    await _dump(main_page, "06_more_options")

    # 第7步：匹配面积档位
    segs = _pick_segments(area_min, area_max)
    log.info("[7] 面积 %.0f~%.0f㎡ → 档位 %s", area_min, area_max, segs)

    # 第8~9步：逐档位搜索
    all_quote_prices = []
    detail_url = None
    for i, seg in enumerate(segs):
        prices, durl = await _search(main_page, community_name, seg)
        all_quote_prices.extend(prices)
        if durl:
            detail_url = durl
        if i < len(segs) - 1:
            await _delay(3, 5)

    log.info("[9] 在售单价共 %d 条", len(all_quote_prices))

    # 第10~12步：详情页
    community_avg = None
    deal_prices = []
    if detail_url:
        community_avg, deal_prices = await _fetch_detail(browser, main_page, detail_url)
    else:
        log.warning("[10] 未找到小区详情链接，跳过详情页")

    status = "SUCCESS" if (all_quote_prices or deal_prices or community_avg) else "NO_DATA"
    return PlatformResult(
        name="贝壳", status=status,
        community_avg_price=community_avg,
        quote_prices=all_quote_prices,
        deal_prices=deal_prices,
    )


async def _search(page, community_name: str, seg: str) -> Tuple[List[float], Optional[str]]:
    """第8步：搜索小区，返回 (在售单价列表, 详情链接)。"""
    log.info("[8] 搜索: %s 档位=%s", community_name, seg)

    # 点击搜索框 → 清空 → 输入小区名（全 nodriver Element API）
    inp = await page.select("#searchInput", timeout=5)
    await inp.click()
    await page
    # 清空：全选删除
    await inp.send_keys("\uE009a")  # Ctrl+A
    await page.keyboard.send("\uE017")  # Delete
    await page
    await _delay(0.5, 1)

    # 输入小区名
    await inp.send_keys(community_name)
    await page
    await _delay(1, 2)

    # 回车搜索
    await page.keyboard.send("\r")  # Enter
    await page
    await _delay(2, 4)

    cur_url = page.target.url
    log.info("  搜索后URL: %s", cur_url)
    await _dump(page, f"08_search_{seg}")

    # 解析搜索结果
    html = await page.get_content()
    prices = parsers.parse_listing_prices(html)
    detail_url = parsers.find_detail_link(html)

    # 提取小区ID，按档位精确筛选
    xiaoqu_id = None
    if detail_url:
        m = re.search(r"/xiaoqu/(\d+)", detail_url)
        if m:
            xiaoqu_id = m.group(1)

    if xiaoqu_id:
        seg_url = f"https://sz.ke.com/ershoufang/{seg}c{xiaoqu_id}/"
        log.info("  档位筛选: %s", seg_url)
        await page.get(seg_url)
        await page.select("#searchInput", timeout=10)  # 等加载
        await page
        await _delay(2, 3)
        html = await page.get_content()
        prices = parsers.parse_listing_prices(html)
        if not detail_url:
            detail_url = parsers.find_detail_link(html)
        await _dump(page, f"08b_segment_{seg}")

    log.info("  档位%s: 单价 %d 条", seg, len(prices))
    return prices, detail_url


async def _fetch_detail(browser, main_page, detail_url: str) -> Tuple[Optional[float], List[float]]:
    """第10~12步：真人点击'查看小区详情'新开tab，抓均价+成交单价。"""
    log.info("[10] 点击查看小区详情")

    # 第10步：真人点击搜索结果里的详情链接（target=_blank 新开tab）
    detail_link = await main_page.select("a.agentCardResblockLink", timeout=5)
    if detail_link:
        old_tab_count = len(browser.tabs)
        await detail_link.click()
        await main_page
        await _delay(3, 5)

        # 找新开的 tab
        detail_tab = None
        if len(browser.tabs) > old_tab_count:
            detail_tab = browser.tabs[-1]
        else:
            for t in browser.tabs:
                if "/xiaoqu/" in (t.target.url or ""):
                    detail_tab = t
                    break
        if not detail_tab:
            log.warning("  未找到详情页tab")
            return None, []
    else:
        log.warning("  未找到详情链接元素，用URL打开")
        detail_tab = await browser.get(detail_url, new_tab=True)

    await detail_tab
    await _delay(2, 3)

    html = await detail_tab.get_content()

    # 第11步：小区均价 + 成交单价
    community_avg = parsers.parse_community_avg_price(html)
    deal_prices = parsers.parse_deal_prices(html)
    log.info("[11] 小区均价=%s 成交单价=%d条", community_avg, len(deal_prices))
    await _dump(detail_tab, "11_detail")

    # 第15步：详情tab后台停留关闭（不阻塞返回）
    asyncio.ensure_future(_close_tab_later(detail_tab))

    return community_avg, deal_prices


async def _close_tab_later(tab):
    """第15步：详情页停留60s后关闭（后台，不阻塞）。"""
    try:
        log.info("[15] 详情页停留 %ds 后关闭", config.DETAIL_TAB_LINGER_SECONDS)
        await asyncio.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        await tab.close()
        log.info("[15] 详情页已关闭")
    except Exception as e:
        log.warning("[15] 关闭异常: %s", e)
