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
import re
import time
from typing import Optional

from app.core import config
from app.utils.debug_utils import dump_html
from app.core.models import ListingSnapshot, PlatformResult
from app.platforms.ajk_constants import START_URL

log = logging.getLogger(__name__)


# ============================================================
# 风控 / 登录判定
# ============================================================

def _is_captcha_url(url: str) -> bool:
    """安居客 58 系验证码拦截页 URL 特征。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def _is_captcha_html(html: str) -> bool:
    markers = (
        "请输入验证码",
        "验证后继续访问",
        "请完成验证",
        "滑动验证",
    )
    return any(marker in html for marker in markers)


def _is_login_html(html: str) -> bool:
    markers = (
        "请输入手机号",
        "请输入密码",
        "手机快捷登录",
        "扫码登录",
    )
    return any(marker in html for marker in markers)


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """安居客风控/登录检测。"""
    if _is_captcha_url(url) or _is_captcha_html(html or ""):
        return True, "命中验证码拦截"
    if _is_login_html(html or ""):
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


async def _human_click(page, element, label: str) -> bool:
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
    for clicker in ("js", "mouse"):
        try:
            if clicker == "js":
                await element.click()
            else:
                await element.mouse_click()
            await page
            await asyncio.sleep(0.8)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s click failed: %s", label, last_error)
    return False


# ============================================================
# 面积自定义输入（安居客是填值+点确定，不是预设档位）
# ============================================================

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
# HTML 解析
# ============================================================

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
            if len(nums) >= 2:
                layout = f"{nums[0]}室{nums[1]}厅"

        area = _extract_first(r'([\d.]+)\s*㎡', chunk)

        name_m = re.search(
            r'property-content-info-comm-name[^>]*>([^<]+)<', chunk
        )
        community_name = name_m.group(1).strip() if name_m else None

        total_price = _extract_first(
            r'property-price-total-num[^>]*>\s*([\d,]+)', chunk
        )
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


def parse_community_avg_price(html: str) -> Optional[float]:
    """从结果页顶部社区卡片提取挂牌均价。

    安居客结果页顶部社区信息卡：
      <div class="community-info-detail-price">
        <p class="community-info-detail-price-money"><em>84307</em>元/㎡</p>
      </div>

    注意：安居客无成交记录，业务上把挂牌均价当作 deal_prices 的替代，
    让 decide() 正常按"在售均价 vs 成交均价"对比出最终价。
    """
    m = re.search(
        r'community-info-detail-price-money[^>]*>\s*<em[^>]*>\s*([\d,]+)\s*</em>\s*元\s*/?\s*㎡',
        html,
    )
    return float(m.group(1).replace(",", "")) if m else None


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

async def reset_to_start_page(page):
    """回到安居客二手房首页，并获取新的页面上下文。"""
    refreshed_page = await page.get(START_URL)
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
    if _is_login_html(html):
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
    area_min: float,
    area_max: float,
    request_id: Optional[str] = None,
) -> PlatformResult:
    """执行一次完整的安居客询价采集。"""
    start = time.time()
    log.info("收到请求: 小区=%s 面积=%.0f~%.0f㎡", community_name, area_min, area_max)
    try:
        return await _do_collect(
            browser=browser,
            main_page=main_page,
            community_name=community_name,
            area_min=area_min,
            area_max=area_max,
            request_id=request_id,
            started_at=start,
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
    area_min: float,
    area_max: float,
    request_id: Optional[str],
    started_at: float,
) -> PlatformResult:
    # 1. 刷新首页保活
    main_page = await reset_to_start_page(main_page)
    await _dump(main_page, "ajk_refresh")

    # 2. 搜索小区
    keyword_html = await _search_community(main_page, community_name)
    await _dump(main_page, "ajk_keyword_result")
    keyword_url = main_page.target.url or ""

    # 3. 判风控/登录
    if _is_captcha_url(keyword_url) or _is_captcha_html(keyword_html):
        return PlatformResult(
            name="安居客",
            status="WAIT_MANUAL_VERIFY",
            reason="搜索后命中验证码拦截",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )
    if _is_login_html(keyword_html):
        return PlatformResult(
            name="安居客",
            status="LOGIN_EXPIRED",
            reason="搜索后进入登录页",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    # 4. 填面积筛选
    log.info("填写面积筛选: %d-%d", area_min, area_max)
    area_confirmed = await _fill_area_inputs(main_page, area_min, area_max)
    await _dump(main_page, "ajk_after_area")

    area_url = main_page.target.url or ""
    area_html = await main_page.get_content()
    if _is_captcha_url(area_url) or _is_captcha_html(area_html):
        return PlatformResult(
            name="安居客",
            status="WAIT_MANUAL_VERIFY",
            reason="面积筛选后命中验证码拦截",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )
    if not area_confirmed:
        return PlatformResult(
            name="安居客",
            status="ERROR",
            reason="面积筛选未能成功提交",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    # 5. 滚动到底（安居客无分页，单页全展示）
    await _scroll_to_bottom(main_page)
    area_html = await main_page.get_content()

    # 6. 解析在售房源
    snapshots = parse_listing_snapshots(area_html)
    quote_prices = [s.unit_price for s in snapshots if s.unit_price]
    if not quote_prices:
        return PlatformResult(
            name="安居客",
            status="NO_DATA",
            reason="面积结果页未抓到在售单价",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    # 7. 挂牌均价（安居客无成交记录，挂牌均价顶替 deal_prices）
    listing_price = parse_community_avg_price(area_html)
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
