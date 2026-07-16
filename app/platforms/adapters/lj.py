# -*- coding: utf-8 -*-
"""链家平台采集适配逻辑。

业务流程与贝壳一致（搜索→筛选→抓在售→点详情→抓成交→算最终价），
但因平台特性有以下差异（链家是贝壳子公司，DOM 高度相似但有差异）：

- 搜索：#searchInput + button.searchButton 真人点击（贝壳用回车提交）
- 面积筛选：需先点"更多选项"展开筛选区 → 点面积区"更多及自定义"展开
  customFilter → 填 min-max input → 确定（贝壳直接点预设档位 a1-a7）
- 在售分页：有分页，翻页前真人滚动停留 + 风控检测（被拦暂停等人工）
- 在售解析：截断到"猜你喜欢"之前，排除推荐位（和安居客 list-guess-title 同类）
- 成交记录：详情页点"查看全部成交记录"→成交列表页翻页抓取
- 成交筛选规则：严格面积区间 + 近半年（和房天下一致；贝壳用 ±20% 容差）

风控检测：链家自己维护标记词（链家和贝壳共用安全系统，
标记词"人机验证"/"贝壳信息安全中心"/"CAPTCHA" 等），不依赖外部通用实现。

采集逻辑移植自 lj_mvp_test.py 全链路验证通过的实现。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Optional

from app.core import config
from app.utils.debug_utils import dump_html
from app.core.models import PlatformResult
from app.parsers import lj as parsers
from app.platforms.base import wait_for_manual_unblock, human_linger, _human_click, click_area_segment
from app.platforms.lj_constants import START_URL

log = logging.getLogger(__name__)


# ============================================================
# 风控 / 登录判定（链家自己的标记词）
# ============================================================

_CAPTCHA_MARKERS = (
    "请输入验证码",
    "验证后继续访问",
    "请完成验证",
    "滑动验证",
    "人机验证",
    "贝壳信息安全中心",
)

_CAPTCHA_URL_MARKERS = ("captcha", "verifycode", "antibot", "antispam")

_LOGIN_MARKERS = (
    "请输入手机号",
    "请输入密码",
    "手机快捷登录",
    "扫码登录",
)


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """链家风控/登录检测。

    链家和贝壳共用安全系统（贝壳信息安全中心），标记词高度相似。
    """
    url_lower = (url or "").lower()
    if any(marker in url_lower for marker in _CAPTCHA_URL_MARKERS):
        return True, "命中验证码拦截"
    if any(marker in (html or "") for marker in _CAPTCHA_MARKERS):
        return True, "命中验证码拦截"
    if any(marker in (html or "") for marker in _LOGIN_MARKERS):
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


# ============================================================
# 搜索
# ============================================================

async def _search_community(page, community_name: str) -> str:
    """搜索小区：填搜索框 + 真人点击搜索按钮提交。

    DOM:
      <form id="searchForm" action="/ershoufang/rs">
        <input type="text" id="searchInput" ...>
        <button type="submit" class="searchButton" ...>
      </form>
    """
    try:
        inp = await page.select("#searchInput", timeout=3)
    except Exception:
        inp = None
    if inp is None:
        raise RuntimeError("未找到搜索框 #searchInput")

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

    # 真人点击搜索按钮 button.searchButton
    try:
        submit_btn = await page.select("button.searchButton", timeout=3)
    except Exception:
        submit_btn = None
    if submit_btn and await _human_click(page, submit_btn, "search button"):
        await page
        await asyncio.sleep(3)
    else:
        raise RuntimeError("未能点击搜索按钮 button.searchButton")

    return await page.get_content()


# 面积自定义输入（旧逻辑保留，不再调用）
# ============================================================

async def _fill_area_inputs(page, area_min, area_max):
    """链家面积筛选：更多选项 → 面积区 → 更多及自定义 → 填 min-max input → 确定。

    链家 DOM：
      1. div.more.btn-more = "更多选项"（首页需点击展开筛选区）
      2. dl.hide.hasmore 内 dt[title*="面积"] = 面积筛选区
      3. span.btn-showmore = "+ 更多及自定义"（点击后展开 customFilter）
      4. span.customFilter[data-role="area"]
         input[role="minValue"] / input[role="maxValue"] / button.btn-range
    """
    # 1. 检查是否有"更多选项"，有则点击展开
    try:
        more_btn = await page.select("div.more.btn-more", timeout=3)
    except Exception:
        more_btn = None
    if more_btn:
        try:
            btn_text = await more_btn.apply("(el) => el.textContent.trim()")
        except Exception:
            btn_text = ""
        if "更多选项" in (btn_text or ""):
            log.info("点击'更多选项'展开筛选区")
            await _human_click(page, more_btn, "更多选项")
            await page
            await asyncio.sleep(1.5)

    # 2. 找面积区：dl 内 dt[title*="面积"]
    try:
        containers = await page.select_all("dl.hide.hasmore", timeout=3)
    except Exception:
        containers = []

    area_container = None
    for c in containers:
        try:
            tit = await c.apply(
                "(el) => { const t = el.querySelector('dt'); return t ? t.title || t.textContent.trim() : ''; }"
            )
        except Exception:
            tit = ""
        if "面积" in tit:
            area_container = c
            break

    if area_container is None:
        raise RuntimeError("未找到面积筛选区（含 dt[title*=面积] 的 dl）")

    # 3. 点击面积区的"更多及自定义"展开 customFilter
    try:
        btns = await area_container.query_selector_all("span.btn-showmore")
    except Exception:
        btns = []
    if btns:
        log.info("点击'更多及自定义'展开面积自定义输入")
        await _human_click(page, btns[0], "更多及自定义")
        await page
        await asyncio.sleep(1.5)

    # 4. 定位 customFilter[data-role="area"] 下的 min/max input
    try:
        custom = await area_container.query_selector_all("span.customFilter[data-role='area']")
    except Exception:
        custom = []
    if not custom:
        try:
            custom = await area_container.query_selector_all("span.customFilter")
        except Exception:
            custom = []
    if not custom:
        raise RuntimeError("面积区未找到 customFilter")
    custom = custom[0]

    min_inputs = await custom.query_selector_all("input[role='minValue']")
    max_inputs = await custom.query_selector_all("input[role='maxValue']")
    if not min_inputs or not max_inputs:
        raise RuntimeError("面积区未找到 minValue/maxValue input")
    min_el, max_el = min_inputs[0], max_inputs[0]

    # 5. 填下限
    await _human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)

    # 6. 填上限
    await _human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)

    # 7. 点"确定"提交
    confirm_btns = await custom.query_selector_all("button.btn-range")
    confirm_clicked = False
    if confirm_btns:
        confirm_clicked = await _human_click(page, confirm_btns[0], "area confirm")
    if not confirm_clicked:
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
# 在售分页采集（参考贝壳 collect_listing_pages + 风控检测）
# ============================================================

async def _click_listing_page_number(page, page_no: int) -> str:
    """点击在售页码，返回加载完成后的 HTML。

    DOM（和贝壳一致）：house-lst-page-box 下 a[data-page='{n}']
    """
    selector = f".house-lst-page-box a[data-page='{page_no}']"
    try:
        element = await page.select(selector, timeout=3)
    except Exception:
        element = None
    if not element:
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")
    if not await _human_click(page, element, f"listing page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")
    await page
    await asyncio.sleep(3)
    return await page.get_content()



async def _collect_listing_pages(page, first_page_html: str, total_pages: int):
    """逐页采集在售 HTML，合并后返回。参考贝壳 collect_listing_pages。

    每页截断到"猜你喜欢"之前再拼接。
    翻页前真人滚动停留，翻页后用通用 detect_block 检测风控，被拦则暂停等人工。
    """

    def _cut_main(html_text: str) -> str:
        cut = html_text.find("猜你喜欢")
        return html_text[:cut] if cut > 0 else html_text

    all_html_parts: list[str] = [_cut_main(first_page_html)]
    page_counts: list[tuple[int, int]] = []
    last_html = first_page_html

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            # 翻页前真人滚动停留（防风控）
            await human_linger(page, page_no)
            last_html = await _click_listing_page_number(page, page_no)

            # 翻页后用通用风控检测
            current_url = page.target.url or ""
            blocked, reason = detect_block(current_url, last_html)
            if blocked:
                log.warning("第 %d 页翻页后被拦截(%s)，等待人工处理", page_no, reason)
                await wait_for_manual_unblock()
                # 人工处理后重新翻到当前页
                last_html = await _click_listing_page_number(page, page_no)

            all_html_parts.append(_cut_main(last_html))
        else:
            await human_linger(page, page_no)

        last_html = await page.get_content()
        if page_no > 1:
            all_html_parts[-1] = _cut_main(last_html)

        count = len(parsers.parse_listing_snapshots(_cut_main(last_html)))
        page_counts.append((page_no, count))
        log.info("第 %d 页在售: %d 条", page_no, count)

    merged_html = "\n".join(all_html_parts)
    return merged_html, page_counts


# ============================================================
# 小区详情（参考贝壳 click_detail_link）
# ============================================================

async def _wait_for_new_tab(browser, old_tab_ids: set, expected_url, timeout=20):
    """等待新标签页打开，参考 ke_adapter._wait_for_new_tab。"""
    for _ in range(int(timeout / 0.5)):
        await asyncio.sleep(0.5)
        for tab in browser.tabs:
            if id(tab) not in old_tab_ids:
                return tab
            if expected_url and (tab.target.url or "").startswith(expected_url):
                return tab
    return None


async def _click_detail_link(browser, page):
    """点击"查看小区详情"打开新标签页，返回 (是否成功, 详情标签页)。

    DOM（和贝壳一致）: <a class="agentCardResblockLink" target="_blank" href=".../xiaoqu/xxx/">查看小区详情</a>
    """
    old_tab_ids = {id(tab) for tab in browser.tabs}

    detail_link = None
    for selector in ("a.agentCardResblockLink", "a[href*='/xiaoqu/']"):
        try:
            detail_link = await page.select(selector, timeout=4)
        except Exception:
            detail_link = None
        if detail_link:
            break

    if not detail_link:
        try:
            detail_link = await page.find("查看小区详情", timeout=3)
        except Exception:
            detail_link = None

    if not detail_link:
        return False, None

    if not await _human_click(page, detail_link, "detail link"):
        return False, None

    detail_tab = await _wait_for_new_tab(browser, old_tab_ids, "/xiaoqu/")
    return True, detail_tab


# ============================================================
# 成交页分页点击（解析在 parsers/lj.py）
# ============================================================

async def _click_deal_page_number(page, page_no: int) -> str:
    """点击成交页页码，返回加载完成后的 HTML。"""
    selector = f"a[data-page='{page_no}']"
    try:
        element = await page.select(selector, timeout=3)
    except Exception:
        element = None
    if not element:
        raise RuntimeError(f"未找到第 {page_no} 页页码按钮")
    if not await _human_click(page, element, f"deal page {page_no}"):
        raise RuntimeError(f"未能成功点击第 {page_no} 页")
    await page
    await asyncio.sleep(3)
    return await page.get_content()


# ============================================================
# 页面复位
# ============================================================

async def _close_tab_later(tab):
    """详情页停留后关闭。"""
    try:
        await asyncio.sleep(config.DETAIL_TAB_LINGER_SECONDS)
        await tab.close()
    except Exception as exc:
        log.warning("关闭详情标签异常: %s", exc)

async def reset_to_start_page(page):
    """回到链家二手房首页，并获取新的页面上下文。"""
    refreshed_page = await page.get(START_URL)
    await refreshed_page
    await asyncio.sleep(2)
    return refreshed_page


# ============================================================
# 就绪检测 / 保活
# ============================================================

async def probe_ready(main_page) -> tuple[bool, str]:
    """检查当前页是否已登录、未被风控、且能执行搜索。

    正向检测：搜索框 + 页面有房源列表（sellListContent）说明已登录且正常。
    captcha/login 拦截检测保留在 _do_collect 的关键步骤中。
    """
    try:
        await main_page.select("body", timeout=10)
        await main_page
        html = await main_page.get_content()
        current_url = main_page.target.url or ""
    except Exception as exc:
        return False, f"页面不可用: {exc}"

    if "lianjia.com" not in current_url:
        return False, f"未在链家域名，当前 URL: {current_url}"

    try:
        inp = await main_page.select("#searchInput", timeout=3)
        if inp is None:
            return False, "未找到搜索框，页面未就绪"
    except Exception:
        return False, "未找到搜索框，页面未就绪"

    # 已登录的首页一定会有 sellListContent（房源列表区）
    if "sellListContent" not in (html or ""):
        return False, "页面无房源列表，可能未登录或未加载完成"

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
) -> PlatformResult:
    """执行一次完整的链家询价采集。"""
    start = time.time()
    log.info("收到请求: 小区=%s 面积=%.0f㎡", community_name, area)
    try:
        return await _do_collect(
            browser=browser,
            main_page=main_page,
            community_name=community_name,
            area=area,
            request_id=request_id,
            started_at=start,
        )
    except Exception as exc:
        log.exception("采集异常")
        return PlatformResult(
            name="链家",
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
) -> PlatformResult:
    def _elapsed():
        return round(time.time() - started_at, 2)

    # 1. 刷新首页保活
    main_page = await reset_to_start_page(main_page)
    await _dump(main_page, "lj_refresh")

    # 2. 搜索小区
    keyword_html = await _search_community(main_page, community_name)
    await _dump(main_page, "lj_keyword_result")
    keyword_url = main_page.target.url or ""

    # 3. 判搜索成功（正向检测，避免标记词误判）
    search_ok = (
        "sellListContent" in keyword_html
        and community_name in keyword_html
    )
    if not search_ok:
        return PlatformResult(
            name="链家",
            status="WAIT_MANUAL_VERIFY" if "验证" in keyword_html else "NO_DATA",
            reason="搜索未返回有效房源" if "sellListContent" not in keyword_html else f"未匹配到: {community_name}",
            request_id=request_id,
            elapsed_seconds=_elapsed(),
        )

    # 4. 面积筛选（动态读取页面档位，点击对应区间链接）
    area_range = await click_area_segment(main_page, area, parsers.parse_area_segments, "lj")
    await _dump(main_page, "lj_after_area")

    area_url = main_page.target.url or ""
    area_html = await main_page.get_content()

    area_min, area_max = area_range if area_range else (area * 0.8, area * 1.2)
    log.info("[4] 面积筛选区间: %.0f~%.0f (来自档位匹配)", area_min, area_max)

    if area_range is None:
        return PlatformResult(
            name="链家", status="ERROR",
            reason="面积筛选未能成功提交",
            request_id=request_id, elapsed_seconds=_elapsed(),
        )

    # 5. 分页采集在售房源
    total_pages = parsers.parse_listing_total_pages(area_html)
    log.info("在售总页数: %d", total_pages)
    merged_html, page_counts = await _collect_listing_pages(main_page, area_html, total_pages)
    log.info("在售分页完成: 每页 %s", page_counts)

    # 6. 解析在售房源
    snapshots = parsers.parse_listing_snapshots(merged_html)
    quote_prices = [s.unit_price for s in snapshots if s.unit_price]
    if not quote_prices:
        return PlatformResult(
            name="链家", status="NO_DATA",
            reason="面积结果页未抓到在售单价",
            request_id=request_id, elapsed_seconds=_elapsed(),
        )

    # 7. 点开小区详情（新标签）
    log.info("点击查看小区详情")
    detail_clicked, detail_tab = await _click_detail_link(browser, main_page)

    # 8. 详情页抓小区均价 + 点"查看全部成交记录"
    deal_prices = []
    deal_record_dicts = []
    deal_tab2 = None  # 成交记录标签页引用
    if detail_clicked and detail_tab is not None:
        await detail_tab
        await asyncio.sleep(3)
        await _dump(detail_tab, "lj_detail")

        # 点"查看全部成交记录"→成交列表页
        log.info("点击查看全部成交记录")
        deal_clicked = False
        try:
            deal_link = await detail_tab.select("a.btn-large", timeout=4)
        except Exception:
            deal_link = None
        if deal_link:
            old_tab_ids = {id(tab) for tab in browser.tabs}
            if await _human_click(detail_tab, deal_link, "查看全部成交记录"):
                deal_tab2 = await _wait_for_new_tab(browser, old_tab_ids, "/chengjiao/")
                if deal_tab2 is not None:
                    deal_clicked = True
                    await deal_tab2
                    await asyncio.sleep(3)
                    await _dump(deal_tab2, "lj_deal")

                    # 翻页抓取成交记录
                    first_deal_html = await deal_tab2.get_content()
                    total_deal_pages = parsers.parse_deal_total_pages(first_deal_html)
                    log.info("成交页总页数: %d", total_deal_pages)

                    all_deals: list = []
                    for deal_page_no in range(1, total_deal_pages + 1):
                        if deal_page_no > 1:
                            try:
                                page_html = await _click_deal_page_number(deal_tab2, deal_page_no)
                            except Exception as exc:
                                log.warning("翻到成交第 %d 页失败: %s", deal_page_no, exc)
                                break
                        else:
                            page_html = first_deal_html

                        page_deals = parsers.parse_deal_records(page_html)
                        all_deals.extend(page_deals)

                        # 优化：本页已有超半年记录，停止翻页
                        if page_deals:
                            cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
                            oldest = min(d[1] for d in page_deals if d[1])
                            if oldest < cutoff:
                                log.info("成交第 %d 页已有超出半年的记录，停止翻页", deal_page_no)
                                break

                    filtered_deals = parsers.filter_deal_records(all_deals, area_min, area_max, months=6)
                    deal_prices = [d[3] for d in filtered_deals if d[3] is not None]
                    deal_record_dicts = [
                        {"area": d[0], "date": d[1], "price": d[3]}
                        for d in filtered_deals if d[3] is not None
                    ]
                    log.info(
                        "成交记录: 总 %d 条, %.0f-%.0f㎡且近半年 %d 条",
                        len(all_deals), area_min, area_max, len(filtered_deals),
                    )
        if not deal_clicked:
            log.warning("未能打开成交记录页")
    else:
        log.warning("未能打开小区详情页，跳过成交记录采集")

    log.info(
        "在售均价=%.2f 成交均价=%s 在售条数=%d",
        sum(quote_prices) / len(quote_prices),
        f"{sum(deal_prices)/len(deal_prices):.2f}" if deal_prices else "None",
        len(quote_prices),
    )

    # 关闭详情/成交标签，切回主页面
    for t in [detail_tab, deal_tab2 if deal_clicked else None]:
        if t is not None:
            asyncio.ensure_future(_close_tab_later(t))
    try:
        await main_page.activate()
        await main_page
    except Exception as exc:
        log.warning("切回主页面失败: %s", exc)

    return PlatformResult(
        name="链家",
        status="SUCCESS",
        community_avg_price=None,
        quote_prices=quote_prices,
        deal_prices=deal_prices,
        deal_records=deal_record_dicts,
        deal_source="成交记录",
        request_id=request_id,
        detail_url=None,
        elapsed_seconds=_elapsed(),
        listing_snapshots=snapshots,
    )
