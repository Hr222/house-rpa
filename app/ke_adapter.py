# -*- coding: utf-8 -*-
"""贝壳 adapter：用 Playwright 执行采集流程。

流程：
  1. 搜索页：小区名+面积筛选 → 抓在售房源 + 找详情链接
  2. 新标签页打开详情页 → 抓小区均价 + 成交记录
  3. 标签页停留 60s → 关闭（规避风控）

采集与解析分离：adapter 只负责拿 HTML/元素，交给 parsers 解析。
"""
import logging
import time
import urllib.parse
from typing import Optional

from playwright.sync_api import Page, BrowserContext, TimeoutError as PWTimeout

import config
from app import parsers
from app.models import PlatformResult, Listing, DealRecord

log = logging.getLogger(__name__)

# 贝壳面积档位 → URL 段（实测：a3=70-90㎡）。MVP 用面积档位近似。
# 注：需求要求 ±20%，但页面只支持档位。后端传基准面积，adapter 选最近档位。
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


def collect(page: Page, community_name: str, area: float,
            xiaoqu_id: Optional[str] = None) -> PlatformResult:
    """在已登录的页面上执行贝壳采集。返回单平台结果。

    page: 已登录、停在贝壳某页的主会话页
    xiaoqu_id: 可选，已知小区ID时直接用，否则从搜索结果找
    """
    start = time.time()
    try:
        return _do_collect(page, community_name, area, xiaoqu_id)
    except PWTimeout:
        return PlatformResult(name="贝壳", status="TIMEOUT",
                              reason="采集超时")
    except Exception as e:
        log.exception("贝壳采集异常")
        return PlatformResult(name="贝壳", status="ERROR", reason=str(e))
    finally:
        log.info("贝壳采集耗时 %.1fs", time.time() - start)


def _check_blocked(page: Page) -> bool:
    """检测是否被风控/掉登录。"""
    url = page.url or ""
    return "captcha" in url or "clogin" in url or "login" in url


def _do_collect(page: Page, community_name: str, area: float,
                xiaoqu_id: Optional[str]) -> PlatformResult:
    # --- Step 1: 搜索页（当前 tab）---
    seg = _pick_area_segment(area)
    if xiaoqu_id:
        search_url = f"https://sz.ke.com/ershoufang/{seg}c{xiaoqu_id}/"
    else:
        enc = urllib.parse.quote(community_name)
        search_url = f"https://sz.ke.com/ershoufang/{seg}rs{enc}/"
    log.info("搜索: %s", search_url)
    page.goto(search_url, wait_until="domcontentloaded",
              timeout=config.REQUEST_TIMEOUT_SECONDS * 1000)
    time.sleep(2)

    if _check_blocked(page):
        reason = "登录态失效，需人工重新登录" if "login" in page.url else "触发验证码"
        status = "LOGIN_EXPIRED" if "login" in page.url else "BLOCKED"
        return PlatformResult(name="贝壳", status=status, reason=reason)

    # 解析在售房源
    search_html = page.content()
    raw_listings = parsers.parse_listings(search_html)
    listings = [Listing(**l) for l in raw_listings]
    log.info("搜索页解析到 %d 条在售房源", len(listings))

    # 找详情链接
    detail_url = parsers.find_detail_link(search_html)
    if not detail_url and xiaoqu_id:
        detail_url = f"https://sz.ke.com/xiaoqu/{xiaoqu_id}/"

    # --- Step 2: 新标签页打开详情页 ---
    community_avg = None
    deals = []
    if detail_url:
        community_avg, deals = _fetch_detail(page.context, detail_url)

    status = "SUCCESS" if (listings or deals or community_avg) else "NO_DATA"
    return PlatformResult(
        name="贝壳", status=status,
        community_avg_price=community_avg,
        listings=listings,
        deals=deals,
    )


def _fetch_detail(context: BrowserContext, detail_url: str):
    """新标签页抓详情页，停留 60s 后关闭。返回 (小区均价, 成交记录列表)。"""
    log.info("打开详情页(新tab): %s", detail_url)
    detail_page = context.new_page()
    try:
        detail_page.goto(detail_url, wait_until="domcontentloaded",
                         timeout=config.REQUEST_TIMEOUT_SECONDS * 1000)
        time.sleep(2)
        if _check_blocked(detail_page):
            log.warning("详情页被风控，跳过成交记录")
            return None, []
        html = detail_page.content()
        avg = parsers.parse_community_avg_price(html)
        raw_deals = parsers.parse_deals(html)
        deals = [DealRecord(**d) for d in raw_deals]
        log.info("详情页: 小区均价=%s, 成交记录=%d条", avg, len(deals))
        # 停留模拟真人浏览（规避风控）
        log.info("详情页停留 %ds 模拟浏览", config.DETAIL_TAB_LINGER_SECONDS)
        time.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        return avg, deals
    finally:
        detail_page.close()
