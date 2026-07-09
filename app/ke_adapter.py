# -*- coding: utf-8 -*-
"""贝壳 adapter（nodriver 版）：用 nodriver 执行采集流程。

nodriver 优势：navigator.webdriver=False，绕过贝壳对 Playwright 的反爬检测。
注意：nodriver 是异步的（async/await）。

流程：
  1. 搜索页：小区名+面积筛选 → 抓在售房源 + 找详情链接
  2. 新标签页打开详情页 → 抓小区均价 + 成交记录
  3. 标签页停留 60s → 关闭（规避风控）

采集与解析分离：adapter 只负责拿 HTML，交给 parsers 解析。
"""
import asyncio
import logging
import time
import urllib.parse
from typing import Optional

import nodriver

import config
from app import parsers
from app.models import PlatformResult, Listing, DealRecord

log = logging.getLogger(__name__)

# Edge 浏览器路径（本机已装）
BROWSER_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 贝壳面积档位 → URL 段（实测：a3=70-90㎡）
AREA_SEGMENTS = [
    (50, "a1"), (70, "a2"), (90, "a3"), (110, "a4"),
    (140, "a5"), (170, "a6"), (9999, "a7"),
]


def _pick_area_segment(area: float) -> str:
    """基准面积 → 最近的贝壳面积档位 URL 段。"""
    for upper, seg in AREA_SEGMENTS:
        if area <= upper:
            return seg
    return "a7"


def _is_blocked(url: str) -> bool:
    """检测是否被风控/掉登录。"""
    return "captcha" in url or "clogin" in url or "login" in url


async def _get_html(tab) -> str:
    """获取当前页面 HTML。"""
    return await tab.evaluate(
        "document.documentElement.outerHTML", return_by_value=True)


async def collect(browser, community_name: str, area_min: float, area_max: float,
                  xiaoqu_id: Optional[str] = None) -> PlatformResult:
    """在已登录的浏览器上执行贝壳采集。返回单平台结果。

    browser: nodriver.Browser（已登录的常驻浏览器）
    area_min/area_max: 后端给定的面积区间，用于成交记录筛选
    """
    start = time.time()
    try:
        return await _do_collect(browser, community_name, area_min, area_max, xiaoqu_id)
    except asyncio.TimeoutError:
        return PlatformResult(name="贝壳", status="TIMEOUT", reason="采集超时")
    except Exception as e:
        log.exception("贝壳采集异常")
        return PlatformResult(name="贝壳", status="ERROR", reason=str(e))
    finally:
        log.info("贝壳采集耗时 %.1fs", time.time() - start)


async def _do_collect(browser, community_name: str, area_min: float, area_max: float,
                      xiaoqu_id: Optional[str]) -> PlatformResult:
    # --- Step 1: 搜索页（新 tab）---
    # 搜索 URL 用区间中点对应的贝壳面积档位（页面只支持档位）
    mid_area = (area_min + area_max) / 2
    seg = _pick_area_segment(mid_area)
    if xiaoqu_id:
        search_url = f"https://sz.ke.com/ershoufang/{seg}c{xiaoqu_id}/"
    else:
        enc = urllib.parse.quote(community_name)
        search_url = f"https://sz.ke.com/ershoufang/{seg}rs{enc}/"
    log.info("搜索: %s", search_url)

    search_tab = await browser.get(search_url, new_tab=True)
    await asyncio.sleep(3)  # 等页面加载 + 风控 SDK 初始化

    if _is_blocked(search_tab.target.url):
        reason = "登录态失效，需人工重新登录" if "login" in search_tab.target.url else "触发验证码"
        status = "LOGIN_EXPIRED" if "login" in search_tab.target.url else "BLOCKED"
        await search_tab.close()
        return PlatformResult(name="贝壳", status=status, reason=reason)

    # 解析在售房源
    search_html = await _get_html(search_tab)
    raw_listings = parsers.parse_listings(search_html)
    listings = [Listing(**l) for l in raw_listings]
    log.info("搜索页解析到 %d 条在售房源", len(listings))

    # 找详情链接
    detail_url = parsers.find_detail_link(search_html)
    if not detail_url and xiaoqu_id:
        detail_url = f"https://sz.ke.com/xiaoqu/{xiaoqu_id}/"

    # 关闭搜索 tab（主会话已在详情页用新 tab）
    await search_tab.close()

    # --- Step 2: 新标签页打开详情页 ---
    community_avg = None
    deals = []
    if detail_url:
        community_avg, deals = await _fetch_detail(browser, detail_url)

    status = "SUCCESS" if (listings or deals or community_avg) else "NO_DATA"
    return PlatformResult(
        name="贝壳", status=status,
        community_avg_price=community_avg,
        listings=listings,
        deals=deals,
    )


async def _fetch_detail(browser, detail_url: str):
    """新标签页抓详情页，停留 60s 后关闭。返回 (小区均价, 成交记录列表)。"""
    log.info("打开详情页(新tab): %s", detail_url)
    detail_tab = await browser.get(detail_url, new_tab=True)
    try:
        await asyncio.sleep(3)
        if _is_blocked(detail_tab.target.url):
            log.warning("详情页被风控，跳过成交记录")
            return None, []
        html = await _get_html(detail_tab)
        avg = parsers.parse_community_avg_price(html)
        raw_deals = parsers.parse_deals(html)
        deals = [DealRecord(**d) for d in raw_deals]
        log.info("详情页: 小区均价=%s, 成交记录=%d条", avg, len(deals))
        # 停留模拟真人浏览（规避风控）
        log.info("详情页停留 %ds 模拟浏览", config.DETAIL_TAB_LINGER_SECONDS)
        await asyncio.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        return avg, deals
    finally:
        await detail_tab.close()
