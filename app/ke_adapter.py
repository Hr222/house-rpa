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
import random
import time
import urllib.parse
from pathlib import Path
from typing import Optional, List

from nodriver import cdp

import config
from app import parsers
from app.models import PlatformResult, Listing, DealRecord

log = logging.getLogger(__name__)


async def _human_delay(min_s=2.0, max_s=5.0):
    """模拟真人操作间隔（随机延时）。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


def _unwrap(dsv):
    """把 nodriver deep_serialized_value 的嵌套格式递归解包成原生类型。

    nodriver 对 object 返回 [[key, {type,value}], ...]，
    对 array 返回 [{type,value}, ...]，需要递归还原。
    """
    if isinstance(dsv, list):
        # object 格式 [[key, val], ...]
        if dsv and isinstance(dsv[0], list) and len(dsv[0]) == 2:
            result = {}
            for item in dsv:
                if isinstance(item, list) and len(item) == 2:
                    key, val = item
                    key_str = key.get('value') if isinstance(key, dict) else key
                    result[key_str] = _unwrap(val.get('value') if isinstance(val, dict) else val)
            return result
        # array 格式 [{type,value}, ...]
        return [_unwrap(x.get('value') if isinstance(x, dict) else x) for x in dsv]
    if isinstance(dsv, dict):
        return dsv.get('value')
    return dsv


async def _human_click(page, selector: str, timeout: int = 5) -> bool:
    """真人点击：用 nodriver 原生元素 API（select → scroll_into_view → click）。

    参考 boss-agent 模式：nodriver 的 .click() 内部已处理坐标和真实点击，
    不手搓 CDP 事件，不用 JS .click()。
    """
    try:
        el = await page.select(selector, timeout=timeout)
        if not el:
            log.warning("  [点击] 未找到: %s", selector)
            return False
        # 滚动到可见再点
        try:
            await el.scroll_into_view_if_needed()
        except Exception:
            pass
        await _human_delay(0.3, 0.8)
        await el.click()
        log.info("  [点击] %s ✓", selector)
        return True
    except Exception as e:
        log.warning("  [点击] 失败 %s: %s", selector, e)
        return False

# debug 落盘目录
DEBUG_DIR = Path(__file__).parent.parent / "debug"

# 浏览器路径（从 config 取，adapter 对外暴露方便测试脚本用）
BROWSER_PATH = config.BROWSER_PATH


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


async def _get_html(page) -> str:
    html = await _evaluate(page, "document.documentElement.outerHTML")
    return str(html)

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


async def _type_text(page, text: str):
    """用 nodriver 元素 API 输入文字（真人输入，不用手搓 CDP）。"""
    try:
        el = await page.select('#searchInput', timeout=3)
        await el.send_keys(text)
        log.info("  [输入] %s ✓", text)
    except Exception as e:
        log.warning("  [输入] 失败: %s", e)


async def _press_enter(page):
    """回车提交。用 nodriver 的 keyboard.send。"""
    try:
        await page.keyboard.send("\r")
        log.info("  [回车] ✓")
    except Exception as e:
        log.warning("  [回车] 失败: %s", e)


async def _evaluate(page, js: str):
    """执行 JS 并取真实值（处理 nodriver 的 RemoteObject 包装）。"""
    result = await page.evaluate(js, return_by_value=True)
    if hasattr(result, 'deep_serialized_value') and result.deep_serialized_value:
        return _unwrap(result.deep_serialized_value.value)
    if hasattr(result, 'value') and result.value is not None:
        return result.value
    return result


async def _refresh(page):
    """第5步：刷新界面（保活插口，后面接定时保活逻辑）。"""
    log.info("[5] 刷新界面（保活）")
    await page.evaluate("location.reload()", return_by_value=True)
    # 等页面真正加载完（等搜索框出现），最多等15秒
    for i in range(30):
        await asyncio.sleep(0.5)
        has = await _evaluate(page, "!!document.querySelector('#searchInput')")
        if has:
            log.info("  页面已加载（#searchInput 出现）")
            break
    await _human_delay(1, 2)


async def _click_more_options(page):
    """第6步：真人点击"展开全部"。"""
    log.info("[6] 点击更多选项展开")
    ok = await _human_click(page, '.btn-showmore')
    if not ok:
        log.info("  更多选项可能已展开或不可见，跳过")
    await _human_delay(1.5, 3)


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
            await _human_delay(3, 5)  # 多档位之间间隔，模拟真人

    # 第9步结果：在售房源单价列表
    log.info("[9] 在售单价共 %d 条", len(all_unit_prices))

    # 第10~12步：详情页
    community_avg = None
    deal_prices = []
    if detail_url:
        # 第10步：真人点击"查看小区详情"（target=_blank 会新开tab）
        community_avg, deal_prices = await _fetch_detail_by_click(
            browser, main_page)

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

    # 真人点击搜索框聚焦（nodriver select → click）
    inp = await page.select('#searchInput', timeout=5)
    if inp:
        try:
            await inp.click()
        except Exception:
            pass
        # 清空（全选删除）
        try:
            await inp.send_keys("\u0001")  # Ctrl+A
            await page.keyboard.send("\u0008")  # Backspace
        except Exception:
            pass
    await _human_delay(0.3, 0.8)

    # 输入小区名（nodriver send_keys）
    await _type_text(page, community_name)
    await _human_delay(1, 2)

    # 回车搜索
    await _press_enter(page)
    await page  # 等页面状态稳定（boss-agent 模式）
    await _human_delay(2, 4)

    cur_url = page.target.url
    log.info("  搜索后URL: %s", cur_url)
    await _dump_html(page, f"08_search_{seg}")

    # 从搜索结果提取小区ID
    html = await _get_html(page)
    detail_url = parsers.find_detail_link(html)

    xiaoqu_id = None
    if detail_url:
        import re
        m = re.search(r'/xiaoqu/(\d+)', detail_url)
        if m:
            xiaoqu_id = m.group(1)

    if xiaoqu_id:
        # 按档位 + 小区ID 访问（在当前tab）
        seg_url = f"https://sz.ke.com/ershoufang/{seg}c{xiaoqu_id}/"
        log.info("  档位筛选: %s", seg_url)
        await page.goto(seg_url, wait_until="domcontentloaded", timeout=30000)
        await _human_delay(2, 4)
        html = await _get_html(page)
        if not detail_url:
            detail_url = parsers.find_detail_link(html)
        await _dump_html(page, f"08b_segment_{seg}")

    # 第9步：抓单价
    raw = parsers.parse_listings(html)
    prices = [r["unit_price"] for r in raw if r.get("unit_price")]
    log.info("  档位%s: 单价 %d 条", seg, len(prices))
    return prices, detail_url


async def _fetch_detail_by_click(browser, main_page):
    """第10~12步：真人点击'查看小区详情'打开新tab，抓均价+成交单价。"""
    log.info("[10] 真人点击'查看小区详情'")

    # 记录当前 tab 数量（点击后新 tab 会增加）
    old_tabs = len(browser.tabs)
    # 真人点击 agentCardResblockLink（target=_blank 新开tab）
    clicked = await _human_click(main_page, 'a.agentCardResblockLink')
    if not clicked:
        log.warning("  [10] 未找到'查看小区详情'链接")
        return None, []
    await _human_delay(3, 5)

    # 找新开的 tab
    detail_tab = None
    if len(browser.tabs) > old_tabs:
        detail_tab = browser.tabs[-1]  # 新 tab 是最后一个
    else:
        # 兜底：按 URL 找 xiaoqu 的 tab
        for t in browser.tabs:
            if '/xiaoqu/' in (t.target.url or ''):
                detail_tab = t
                break

    if not detail_tab:
        log.warning("  [10] 未找到新开的详情页tab")
        return None, []

    # 等详情页加载
    await _human_delay(2, 4)
    html = await _get_html(detail_tab)

    # 第11步：小区均价 + 成交记录单价
    community_avg = parsers.parse_community_avg_price(html)
    raw_deals = parsers.parse_deals(html)
    deal_prices = [d["unit_price"] for d in raw_deals if d.get("unit_price")]
    log.info("[11] 小区均价=%s, 成交单价=%d条", community_avg, len(deal_prices))
    await _dump_html(detail_tab, "11_detail")

    # 第15步：详情tab定时停留后关闭（后台，不阻塞返回）
    asyncio.create_task(_close_tab_after_linger(detail_tab))

    return community_avg, deal_prices


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
