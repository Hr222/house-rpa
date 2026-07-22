# -*- coding: utf-8 -*-
"""安居客平台采集适配逻辑。

业务流程与贝壳一致（搜索→筛选→抓在售→算最终价），但因平台特性有以下差异：
- 面积筛选：自定义输入框填值（贝壳是点预设档位 a1-a7）
- 无分页：单页全展示，滚动到底即可（贝壳要翻页）
- 无成交记录：安居客不对外展示成交，业务上把挂牌均价（社区卡片）
  顶替 deal_prices，让 decide() 正常按"在售均价 vs 成交均价"对比出最终价。
  代码注释里已标明这一特殊处理。
- 不点详情：挂牌均价在结果页就有，无需进小区详情页（平台差异，非删流程）。

采集逻辑移植自 ajk_mvp_test.py 全链路验证通过的实现。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional

from app.core import config
from app.platforms.base import (
    _human_click,
    click_area_segment,
    has_matching_community_snapshots,
    listing_filter_summary,
    listing_no_data_reason,
    listing_no_data_status,
    prepare_listing_data,
    short_circuit_result,
    wait_and_reload_after_block,
)
from app.utils.debug_utils import dump_html
from app.core.models import PlatformResult
from app.parsers import ajk as parsers
from app.platforms.ajk_constants import START_URL
from app.platforms.city_map import get_start_url

log = logging.getLogger(__name__)


# ============================================================
# 风控 / 登录判定
# ============================================================

def _is_captcha_url(url: str) -> bool:
    """安居客 58 系验证码拦截页 URL 特征。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def _is_captcha_html(html: str) -> bool:
    """安居客(58系)验证码拦截页 HTML 特征。

    真实样本：callback.58.com/antibot/verifycode，58 自家 ISDCaptcha SDK。
    """
    markers = (
        "请输入验证码",          # <title> 文案（ws:IP 动态后缀，单靠不稳）
        'id="ISDCaptcha"',      # 58 验证码 SDK 容器（最稳的结构标识）
        'class="code_img"',     # 验证码图片容器
    )
    return any(marker in (html or "") for marker in markers)


def _is_login_url(url: str) -> bool:
    url = (url or "").lower()
    return "login" in url or "passport" in url or "signin" in url


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """安居客风控/登录检测。"""
    if _is_captcha_url(url) or _is_captcha_html(html or ""):
        return True, "命中验证码拦截"
    if _is_login_url(url):
        return True, "命中登录页"
    return False, ""


# ============================================================
# 页面交互辅助
# ============================================================

async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    """真人操作间隔。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _dump(page, name: str):
    """调试模式下导出页面 HTML。"""
    await dump_html(page, name, logger=log)


async def _is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def _get_search_input(page):
    """定位安居客搜索框。

    安居客首页搜索框占位符为"请输入小区名称、地址"。
    """
    selectors = [
        "input#search-input",
        "input#searchInput",
        "input.search-input",
        "input[name='keyword']",
        "input[placeholder*='小区']",
        "input[placeholder*='地址']",
        "input[placeholder*='搜索']",
    ]
    for selector in selectors:
        try:
            elements = await page.select_all(selector, timeout=1.5)
        except Exception:
            continue
        for element in elements:
            if await _is_interactable(element):
                return selector, element
        if elements:
            return selector, elements[0]
    return None, None


async def _fill_area_inputs(page, area_min, area_max):
    """定位面积筛选区的自定义输入框并填值。

    安居客结果页里有两处 input.input：一处在价格筛选区（unit=万），
    一处在面积筛选区（unit=㎡）。靠父级 li 内的 unit 文本是"㎡"区分。
    填值后"确定"按钮会显示，需点击提交。

    注意：Element 用 query_selector_all（不是 select_all，那是 Tab 的方法）。
    """
    # 1. 遍历所有 line-item-input，靠 unit 文本是"㎡"定位面积区
    line_items = await page.select_all("li.line-item-input")
    log.info("line-item-input 数量: %d", len(line_items))

    area_li = None
    for li in line_items:
        unit_text = await li.apply(
            "(el) => { const u = el.querySelector('.unit'); return u ? u.textContent.trim() : ''; }"
        )
        if unit_text == "㎡":
            area_li = li
            break

    if area_li is None:
        raise RuntimeError("未找到面积筛选区（unit=㎡ 的 line-item-input）")

    # 2. 取该 li 下两个 input
    inputs = await area_li.query_selector_all("input.input")
    if len(inputs) < 2:
        raise RuntimeError(f"面积区 input 不足 2 个，实际 {len(inputs)} 个")
    min_el, max_el = inputs[0], inputs[1]

    # 3. 填下限
    await _human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)

    # 4. 填上限
    await _human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)

    # 5. 点"确定"提交
    confirms = await area_li.query_selector_all(".confirm")
    confirm_clicked = False
    if confirms:
        confirm_clicked = await _human_click(page, confirms[0], "area confirm")
    if not confirm_clicked:
        # 兜底回车
        try:
            await max_el.send_keys("\r")
            await page
            confirm_clicked = True
        except Exception:
            pass

    await page
    await asyncio.sleep(3)
    return confirm_clicked


# ============================================================
# 滚动到底触发懒加载（安居客无分页，单页全展示）
# ============================================================

async def _scroll_to_bottom(page, max_rounds: int = 20, wait: float = 1.8) -> int:
    """循环滚动到页面底部，触发懒加载，返回实际滚动轮数。

    安居客 Vue 页面：滚动到底会加载下一批房源。
    判定到底：连续两次滚动后 body 高度不变。
    """
    last_height = 0
    rounds = 0
    for i in range(max_rounds):
        try:
            current = await page.evaluate("document.body.scrollHeight", return_by_value=True)
        except Exception:
            current = last_height
        if current == last_height and i > 0:
            log.info("已到底（高度不变），共滚动 %d 轮", rounds)
            break
        last_height = current
        rounds += 1
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await page
        except Exception:
            pass
        await asyncio.sleep(wait)

    return rounds


# ============================================================
# 搜索
# ============================================================

async def _search_community(page, community_name: str) -> str:
    """搜索小区，返回结果页 HTML。"""
    _, inp = await _get_search_input(page)
    if inp is None:
        raise RuntimeError("未找到安居客搜索框")

    if not await _human_click(page, inp, "search input"):
        raise RuntimeError("搜索框未能成功点击")

    try:
        await inp.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.5)
    await inp.send_keys(community_name)
    await page
    await asyncio.sleep(1.0)

    # 回车提交
    await inp.send_keys("\r")
    await page
    await asyncio.sleep(3)

    return await page.get_content()


# ============================================================
# 页面复位
# ============================================================

async def reset_to_start_page(page, city: str = "深圳"):
    """回到安居客二手房首页，并获取新的页面上下文。"""
    url = get_start_url("ajk", city)
    refreshed_page = await page.get(url)
    await refreshed_page
    await asyncio.sleep(2)
    return refreshed_page


# ============================================================
# 就绪检测 / 保活
# ============================================================

async def probe_ready(main_page) -> tuple[bool, str]:
    """检查当前页是否已登录、未被风控、且能执行搜索。"""
    try:
        await main_page.select("body", timeout=10)
        await main_page
        html = await main_page.get_content()
        current_url = main_page.target.url or ""
    except Exception as exc:
        return False, f"页面不可用: {exc}"

    if _is_captcha_url(current_url) or _is_captcha_html(html):
        return False, "命中验证码拦截，等待人工处理"
    if _is_login_url(current_url):
        return False, "当前会话未登录或已失效"

    try:
        _, inp = await _get_search_input(main_page)
        if inp is None:
            return False, "未找到搜索框，页面未就绪"
    except Exception:
        return False, "未找到搜索框，页面未就绪"
    return True, "READY"


async def keepalive(main_page) -> tuple[bool, str]:
    """轻量保活：优先探测，必要时刷新。"""
    ready, message = await probe_ready(main_page)
    if ready:
        try:
            await main_page.evaluate("window.scrollTo(0, 0);")
            await main_page
        except Exception:
            pass
        return True, "READY"

    try:
        main_page = await reset_to_start_page(main_page)
    except Exception as exc:
        return False, f"刷新保活失败: {exc}"

    return await probe_ready(main_page)


# ============================================================
# 采集主体
# ============================================================

async def collect(
    browser,
    main_page,
    community_name: str,
    area: float,
    request_id: Optional[str] = None,
    city: str = "深圳",
) -> PlatformResult:
    """执行一次完整的安居客询价采集。"""
    start = time.time()
    log.info("收到请求: 小区=%s 面积=%.1f㎡ 城市=%s", community_name, area, city)
    try:
        return await _do_collect(
            browser=browser,
            main_page=main_page,
            community_name=community_name,
            area=area,
            request_id=request_id,
            started_at=start,
            city=city,
        )
    except Exception as exc:
        log.exception("采集异常")
        return PlatformResult(
            name="安居客",
            status="ERROR",
            reason=str(exc),
            request_id=request_id,
            elapsed_seconds=round(time.time() - start, 2),
        )


async def _do_collect(
    *,
    browser,
    main_page,
    community_name: str,
    request_id: Optional[str],
    started_at: float,
    area: float,
    city: str = "深圳",
) -> PlatformResult:
    # 1. 刷新首页保活
    main_page = await reset_to_start_page(main_page, city)
    # 采集起点风控兜底：首页若被风控(CAPTCHA/登录失效)，阻塞等人解除后重取
    await wait_and_reload_after_block(main_page, detect_block, "首页")
    await _dump(main_page, "ajk_refresh")

    # 2. 搜索小区
    keyword_html = await _search_community(main_page, community_name)
    await _dump(main_page, "ajk_keyword_result")
    keyword_url = main_page.target.url or ""

    # 3. 判风控/登录
    if _is_captcha_url(keyword_url) or _is_captcha_html(keyword_html):
        return short_circuit_result(
            "安居客", "WAIT_MANUAL_VERIFY", "搜索后命中验证码拦截",
            request_id, started_at,
        )
    if _is_login_url(keyword_url):
        return short_circuit_result(
            "安居客", "LOGIN_EXPIRED", "搜索后进入登录页",
            request_id, started_at,
        )

    # 3.5 无数据短路 + 泛搜索校验
    if 'property-content-info-comm-name' not in keyword_html:
        return short_circuit_result(
            "安居客", "NO_DATA", "关键词搜索无在售房源",
            request_id, started_at,
        )

    keyword_snaps = parsers.parse_listing_snapshots(keyword_html)
    if not has_matching_community_snapshots(keyword_snaps, community_name):
        return short_circuit_result(
            "安居客", "NO_DATA", f"关键词搜索未匹配到小区: {community_name}",
            request_id, started_at,
        )

    # 4. 面积筛选（动态读取页面档位，点击对应区间链接）
    area_range = await click_area_segment(main_page, area, parsers.parse_area_segments, "ajk")
    await _dump(main_page, "ajk_after_area")

    area_url = main_page.target.url or ""
    area_html = await main_page.get_content()
    if _is_captcha_url(area_url) or _is_captcha_html(area_html):
        return short_circuit_result(
            "安居客", "WAIT_MANUAL_VERIFY", "面积筛选后命中验证码拦截",
            request_id, started_at,
        )
    if area_range is None:
        return short_circuit_result(
            "安居客", "NO_DATA", "该面积区间无在售房源（档位已禁用）",
            request_id, started_at,
        )

    # 5. 滚动到底（安居客无分页，单页全展示）
    await _scroll_to_bottom(main_page)
    area_html = await main_page.get_content()

    # 6. 解析在售房源
    parsed_snapshots = parsers.parse_listing_snapshots(area_html)
    snapshots, quote_prices = prepare_listing_data(parsed_snapshots, community_name, area)
    log.info(
        "安居客在售房源最终校验: %s",
        listing_filter_summary(parsed_snapshots, community_name, area),
    )
    if not snapshots:
        return short_circuit_result(
            "安居客", listing_no_data_status(parsed_snapshots, community_name, area),
            listing_no_data_reason(parsed_snapshots, community_name, area),
            request_id, started_at,
        )
    if not quote_prices:
        return short_circuit_result(
            "安居客", "NO_DATA", "面积结果页未抓到在售单价",
            request_id, started_at,
        )

    # 7. 挂牌均价（安居客无成交记录，挂牌均价顶替 deal_prices）
    listing_price = parsers.parse_community_avg_price(area_html)
    # 安居客特殊：无成交记录，把挂牌均价作为 deal_prices 唯一元素，
    # 让 decide() 正常按"在售均价 vs 成交均价"对比出最终价。
    deal_prices = [listing_price] if listing_price is not None else []

    log.info(
        "在售均价=%.2f 挂牌均价(顶替成交)=%s 在售条数=%d",
        sum(quote_prices) / len(quote_prices),
        listing_price,
        len(quote_prices),
    )

    return PlatformResult(
        name="安居客",
        status="SUCCESS",
        community_avg_price=None,
        quote_prices=quote_prices,
        deal_prices=deal_prices,
        deal_source="挂牌均价顶替",
        request_id=request_id,
        detail_url=None,
        elapsed_seconds=round(time.time() - started_at, 2),
        listing_snapshots=snapshots,
    )
