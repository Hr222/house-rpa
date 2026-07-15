# -*- coding: utf-8 -*-
"""乐有家平台采集适配逻辑。

业务流程与贝壳一致（搜索→筛选→抓在售→算最终价），但因平台特性有以下差异：
- 面积筛选：自定义输入框填值（贝壳是点预设档位 a1-a7）
- 搜索走 URL 参数：/esf/?c={小区名}
- 无成交记录：乐有家不对外展示成交，业务上把小区均价（社区信息卡）
  顶替 deal_prices，让 decide() 正常按"在售均价 vs 成交均价"对比出最终价。
  代码注释里已标明这一特殊处理。
- 不点详情：小区均价在结果页社区信息卡就有（平台差异，非删流程）。
- 分页格式：/esf/n{page}/?c={community}&ae={max}&as={min}

采集逻辑移植自 lyj_mvp_test.py 全链路验证通过的实现。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from app.core import config
from app.utils.debug_utils import dump_html
from app.core.models import ListingSnapshot, PlatformResult
from app.platforms.lyj_constants import START_URL
from app.platforms.base import human_linger

log = logging.getLogger(__name__)



# ============================================================
# 风控 / 登录判定
# ============================================================

def _is_captcha_url(url: str) -> bool:
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
    """乐有家风控/登录检测。"""
    if _is_captcha_url(url) or _is_captcha_html(html or ""):
        return True, "命中验证码拦截"
    if _is_login_html(html or ""):
        return True, "命中登录页"
    return False, ""


# ============================================================
# 页面交互辅助
# ============================================================

async def _delay(min_s: float = 1.5, max_s: float = 3.5):
    import random
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _dump(page, name: str):
    await dump_html(page, name, logger=log)


async def _is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


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
            await asyncio.sleep(1.0)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s click failed: %s", label, last_error)
    return False


# ============================================================
# 搜索
# ============================================================

async def _search_community(page, community_name: str) -> str:
    """搜索小区，返回结果页 HTML。

    乐有家搜索走 URL 参数：https://shenzhen.leyoujia.com/esf/?c={community}
    """
    search_url = f"https://shenzhen.leyoujia.com/esf/?c={community_name}"
    await page.get(search_url)
    await page
    await asyncio.sleep(3)
    return await page.get_content()


# ============================================================
# 面积筛选（自定义输入框）
# ============================================================

async def _fill_area_inputs(page, area_min, area_max):
    """乐有家面积筛选：找到"面积"区 → 点"更多及自定义" → 填值 → 点确定。

    页面有两个 hasmore 区域（价格区和面积区），
    靠 .c333.tit 文本为"面积"来区分。
    """
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
        await _human_click(page, btns[0], "btn-showmore")
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
        return False

    min_ok = await _is_interactable(min_el)
    max_ok = await _is_interactable(max_el)

    if not min_ok and not max_ok:
        log.warning("乐有家面积输入框不可交互")
        return False

    if min_ok:
        await _human_click(page, min_el, "area min input")
        try:
            await min_el.clear_input()
        except Exception:
            pass
        await asyncio.sleep(0.3)
        await min_el.send_keys(str(int(area_min)))
        await page
        await asyncio.sleep(0.5)

    if max_ok:
        await _human_click(page, max_el, "area max input")
        try:
            await max_el.clear_input()
        except Exception:
            pass
        await asyncio.sleep(0.3)
        await max_el.send_keys(str(int(area_max)))
        await page
        await asyncio.sleep(0.8)

    try:
        confirm_btn = await page.select("#areaUsedefinedBtn", timeout=3)
    except Exception:
        confirm_btn = None

    confirm_clicked = False
    if confirm_btn:
        confirm_clicked = await _human_click(page, confirm_btn, "area confirm")
    if not confirm_clicked and max_el and max_ok:
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
# HTML 解析：房源快照
# ============================================================

def _normalize_text(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_listing_snapshots(html: str) -> list[ListingSnapshot]:
    """从乐有家搜索结果页提取房源快照。

    每个房源在 <li class="item clearfix"> 内：
      p.tit a              → 标题
      p.attr span          → "3室2厅1卫 / 建筑面积73.5㎡"
      p.attr a[href*=xq/detail] → 小区名链接
      span.salePrice       → 总价数字
      p.sub                → "单价44218元/㎡"
    """
    cut = html.find("猜你喜欢")
    source = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(
        r'<li class="item clearfix"[^>]*>(.*?)</li>', source, re.S
    ):
        chunk = block.group(1)

        community_name = None
        comm_m = re.search(r'href="/xq/detail/\d+[^"]*"[^>]*>(?:<[^>]+>)*\s*([^<]+)', chunk)
        if comm_m:
            community_name = _normalize_text(comm_m.group(1))

        layout = None
        layout_m = re.search(r"(\d+室\d+厅)", chunk)
        if layout_m:
            layout = layout_m.group(1)

        area = None
        area_m = re.search(r"建筑面积\s*([\d.]+)\s*㎡", chunk)
        if area_m:
            area = float(area_m.group(1))

        total_price = None
        tp_m = re.search(r'salePrice[^>]*>\s*([\d,]+)\s*<', chunk)
        if tp_m:
            total_price = float(tp_m.group(1).replace(",", ""))

        unit_price = None
        up_m = re.search(r'<p class="sub">.*?([\d,]+)\s*元\s*/?\s*㎡', chunk)
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


# ============================================================
# HTML 解析：小区均价
# ============================================================

def _parse_community_avg_price(html: str) -> Optional[float]:
    """从结果页社区信息卡提取小区均价。

    DOM: <em class="label">小区均价</em><em class="txt">54386元/㎡</em>

    乐有家无成交记录，业务上用小区均价顶替 deal_prices。
    """
    m = re.search(r"小区均价</em>\s*<em\s[^>]*>\s*([\d,]+)\s*元", html)
    return float(m.group(1).replace(",", "")) if m else None


# ============================================================
# 分页解析与导航
# ============================================================

def _parse_total_pages(html: str) -> int:
    """解析总页数。尾页链接: <a title="N">尾页</a>"""
    m = re.search(r'<a[^>]*title="(\d+)"[^>]*>尾页</a>', html or "")
    return int(m.group(1)) if m else 1


def _parse_current_page(html: str) -> int:
    m = re.search(r'<a[^>]*class="on"[^>]*href="[^"]*">(\d+)</a>', html or "")
    return int(m.group(1)) if m else 1


async def _wait_for_results_loaded(page, expected_page: int, timeout: float = 15) -> str:
    deadline = asyncio.get_event_loop().time() + timeout
    last_html = ""
    while asyncio.get_event_loop().time() < deadline:
        last_html = await page.get_content()
        if _parse_current_page(last_html) == expected_page:
            await asyncio.sleep(1.2)
            return last_html
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.2)
    return last_html or await page.get_content()


async def _click_page_number(page, page_no: int) -> str:
    """点击页码按钮，返回加载完成后的 HTML。"""
    try:
        elements = await page.select_all(f'a[title="{page_no}"]', timeout=3)
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
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")

    if not await _human_click(page, target, f"page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")

    return await _wait_for_results_loaded(page, expected_page=page_no)



async def _collect_listing_pages(page, first_page_html: str, total_pages: int):
    """逐页采集在售房源快照。"""
    all_snapshots: list[ListingSnapshot] = []
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            last_html = await _click_page_number(page, page_no)

        await human_linger(page, 0)
        last_html = await page.get_content()
        await _dump(page, f"lyj_area_page_{page_no}")

        page_snapshots = _parse_listing_snapshots(last_html)
        all_snapshots.extend(page_snapshots)
        log.info("乐有家第 %d/%d 页: %d 条", page_no, total_pages, len(page_snapshots))

    return all_snapshots, last_html


# ============================================================
# 页面复位 / 就绪检测 / 保活
# ============================================================

async def reset_to_start_page(page):
    """回到乐有家二手房首页，并获取新的页面上下文。"""
    refreshed_page = await page.get(START_URL)
    await refreshed_page
    await asyncio.sleep(2)
    return refreshed_page


async def probe_ready(main_page) -> tuple[bool, str]:
    """检查当前页是否已登录、未被风控、且能执行操作。"""
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

    # 已登录的首页一定会有筛选区
    try:
        await main_page.select("div.selected-index", timeout=3)
    except Exception:
        return False, "未找到筛选区，页面可能未登录或未加载完成"

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
    """执行一次完整的乐有家询价采集。"""
    start = time.time()
    log.info("乐有家收到请求: 小区=%s 面积=%.0f~%.0f㎡", community_name, area_min, area_max)
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
        log.exception("乐有家采集异常")
        return PlatformResult(
            name="乐有家",
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
    await _dump(main_page, "lyj_refresh")

    # 2. 搜索小区
    keyword_html = await _search_community(main_page, community_name)
    await _dump(main_page, "lyj_keyword_result")
    keyword_url = main_page.target.url or ""

    # 3. 判风控/登录/无数据
    if _is_captcha_url(keyword_url) or _is_captcha_html(keyword_html):
        return PlatformResult(
            name="乐有家",
            status="WAIT_MANUAL_VERIFY",
            reason="搜索后命中验证码拦截",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )
    if _is_login_html(keyword_html):
        return PlatformResult(
            name="乐有家",
            status="LOGIN_EXPIRED",
            reason="搜索后进入登录页",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )
    if "很抱歉，没有找到" in keyword_html:
        log.info("乐有家无匹配小区: %s，记作在售0/成交0", community_name)
        return PlatformResult(
            name="乐有家",
            status="SUCCESS",
            reason=f"乐有家无{community_name}在售记录和成交记录",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
            deal_source="无数据",
        )

    # 4. 填面积筛选
    log.info("乐有家填写面积筛选: %d-%d", area_min, area_max)
    area_confirmed = await _fill_area_inputs(main_page, area_min, area_max)
    await _dump(main_page, "lyj_after_area")

    area_url = main_page.target.url or ""
    area_html = await main_page.get_content()
    if _is_captcha_url(area_url) or _is_captcha_html(area_html):
        return PlatformResult(
            name="乐有家",
            status="WAIT_MANUAL_VERIFY",
            reason="面积筛选后命中验证码拦截",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )
    if not area_confirmed:
        return PlatformResult(
            name="乐有家",
            status="ERROR",
            reason="面积筛选未能成功提交",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    # 5. 分页采集在售房源
    total_pages = _parse_total_pages(area_html)
    log.info("乐有家总页数: %d", total_pages)
    listing_snapshots, last_page_html = await _collect_listing_pages(
        main_page, area_html, total_pages
    )

    quote_prices = [s.unit_price for s in listing_snapshots if s.unit_price]
    if not quote_prices:
        return PlatformResult(
            name="乐有家",
            status="NO_DATA",
            reason="面积结果页未抓到在售单价",
            request_id=request_id,
            elapsed_seconds=round(time.time() - started_at, 2),
        )

    # 6. 小区均价（乐有家无成交记录，挂牌均价顶替 deal_prices）
    listing_price = _parse_community_avg_price(area_html)
    # 乐有家特殊：无成交记录，把小区均价作为 deal_prices 唯一元素，
    # 让 decide() 正常按"在售均价 vs 成交均价"对比出最终价。
    deal_prices = [listing_price] if listing_price is not None else []

    quote_avg = sum(quote_prices) / len(quote_prices)
    deal_avg = listing_price
    log.info(
        "乐有家在售均价=%.2f 小区均价(顶替成交)=%s 在售条数=%d",
        quote_avg, listing_price, len(quote_prices),
    )

    return PlatformResult(
        name="乐有家",
        status="SUCCESS",
        community_avg_price=None,
        quote_prices=quote_prices,
        deal_prices=deal_prices,
        deal_source="小区均价顶替",
        request_id=request_id,
        detail_url=None,
        elapsed_seconds=round(time.time() - started_at, 2),
        listing_snapshots=listing_snapshots,
    )
