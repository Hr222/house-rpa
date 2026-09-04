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
- 成交筛选规则：请求面积 ±5㎡ + 近半年；不使用在售页面的宽面积档位。

风控检测：链家自己维护标记词（链家和贝壳共用安全系统，
标记词"人机验证"/"贝壳信息安全中心"/"CAPTCHA" 等），不依赖外部通用实现。

采集逻辑移植自 lj_mvp_test.py 全链路验证通过的实现。
"""

from __future__ import annotations
import asyncio
import logging
import re
import time
from html import unescape
from typing import Optional
from urllib.parse import urljoin, urlparse

from app.rpa.core.status import PlatformResultStatus
from app.rpa.core.models import PlatformResult
from app.rpa.platforms.lj import parser as parsers
from app.rpa.utils.debug_utils import dump_html
from app.rpa.platforms.base import _human_click, has_matching_community_snapshots, wait_and_reload_after_block
from app.rpa.platforms.city_map import get_start_url


# ============================================================
# 风控 / 登录判定（链家自己的标记词）
# ============================================================

# 验证码 HTML 特征（基于真实 dump：hip.lianjia.com/captcha 页面）
# hip 安全中心 + 极验 geetest SDK，链家与贝壳共用同一套安全系统
# 注意：只留验证码页专属标识。"贝壳信息安全中心"(页脚版权)和
# "hip-static"(静态资源路径)在正常结果页也存在，曾导致正常页误判成
# 验证码（见 2026-07-17 中海怡翠山庄 case），已移除。
_CAPTCHA_MARKERS = (
    "<title>CAPTCHA</title>",       # 验证码页 title（铁证，正常页不会有）
    "captcha.lianjia.com",          # window.captchaEndpoint JS 变量
    'alt="CAPTCHA"',                 # <img class="bg" alt="CAPTCHA">
)

# 验证码 URL 特征（真实样本：https://hip.lianjia.com/captcha?location=...）
_CAPTCHA_URL_MARKERS = (
    "hip.lianjia.com/captcha",      # 最精确（真实样本域名）
    "/captcha",                      # 兜底
)

_LOGIN_URL_MARKERS = ("login", "passport", "signin")

# 登录失效检测：链家/贝壳共用 ljConf，未登录时后端注入的 ucid 为空字符串
# 真实样本：未登录 ucid:'' / 已登录 ucid:'2000000547667569'
# 用"紧跟 cdn"锚定 ljConf 上下文，排除页面里其它无关的 ucid:''（SDK 配置项）
_LJ_NOT_LOGIN_PATTERN = re.compile(r"ucid\s*:\s*''\s*,?\s*cdn", re.S)


log = logging.getLogger(__name__)
def _is_login_expired_html(html: str) -> bool:
    """链家登录失效（软失效）：ljConf 内 ucid 为空。

    链家页面常驻登录弹窗 DOM，不能用"请输入手机号"等词判断（已登录页也有）。
    ucid 是后端注入的用户 id，未登录为空字符串、已登录为数字，最可靠。
    "紧跟 cdn" 锚定 ljConf，避免误匹配页面里其它无关的 ucid:''。
    """
    return bool(_LJ_NOT_LOGIN_PATTERN.search(html or ""))


def detect_block(url: str, html: str) -> tuple[bool, str]:
    """链家风控/登录检测。

    链家和贝壳共用安全系统（贝壳信息安全中心），标记词高度相似。
    登录检测覆盖两种失效形态：硬失效(URL含login/passport) + 软失效(ucid为空)。
    """
    url_lower = (url or "").lower()
    if any(marker in url_lower for marker in _CAPTCHA_URL_MARKERS):
        return True, "命中验证码拦截"
    if any(marker in (html or "") for marker in _CAPTCHA_MARKERS):
        return True, "命中验证码拦截"
    if (any(marker in url_lower for marker in _LOGIN_URL_MARKERS)
            or _is_login_expired_html(html)):
        return True, "命中登录页"
    return False, ""


def is_no_result(html: str) -> bool:
    """空态判定：链家"暂无在售房源"页（与贝壳共用同一套列表组件）。

    空态页仍有小区详情/列表结构，必须靠 m-noresult 在解析前短路，
    绝不能把推荐位当在售解析。marker 与贝壳同源（ke_adapter.is_no_result）。
    """
    return "m-noresult" in (html or "")


# ============================================================
# 页面交互辅助
# ============================================================

async def _dump(page, name: str):
    """调试模式下导出页面 HTML。"""
    await dump_html(page, name, logger=log)


async def _click_deal_page_number(page, page_no: int) -> Optional[str]:
    """点击成交页页码，返回加载完成后的 HTML；无法翻页时返回 None（优雅停止信号）。

    核心逻辑（找不到按钮→风控检测→恢复重试→点击）由 base.safe_select_and_click 统一处理，
    本函数只保留链家特有的点击后等待逻辑（await page + sleep + get_content）。
    """
    selector = f"a[data-page='{page_no}']"
    element = await safe_select_and_click(
        page, selector,
        dump_fn=_dump,
        dump_name=f"lj_deal_page_{page_no}_no_button",
        detect_fn=detect_block,
        block_label=f"成交第 {page_no} 页(翻页前-按钮缺失)",
        click_label=f"deal page {page_no}",
    )
    if element is None:
        return None
    await page
    await asyncio.sleep(3)
    return await page.get_content()


# ============================================================
# 页面复位
# ============================================================

async def reset_to_start_page(page, city: str = "深圳"):
    """回到链家二手房首页，并获取新的页面上下文。"""
    url = get_start_url("lj", city)
    refreshed_page = await page.get(url)
    await refreshed_page
    await asyncio.sleep(2)
    return refreshed_page


# ============================================================
# 就绪检测 / 保活
# ============================================================

async def probe_ready(main_page) -> tuple[bool, str]:
    """轻量就绪探测（URL 直达时代）：页面可用且未风控即 READY。"""
    try:
        await main_page.select("body", timeout=8)
        await main_page
        html = await main_page.get_content()
    except Exception as exc:
        return False, f"页面不可用: {exc}"
    blocked, reason = detect_block(main_page.target.url or "", html)
    if blocked:
        return False, reason
    return True, "READY"


def _token_from_url(page_url: str) -> Optional[str]:
    """从链家挂牌/成交入口 URL 提取 c{ID} 小区 token。"""
    m = re.search(r"/(?:ershoufang|chengjiao)/(c\d+)", page_url or "")
    return m.group(1) if m else None


def _build_pagination_urls(page_url: str, first_page_html: str, max_pages: int = 50) -> list[str]:
    """由第 1 页原生分页链接收集后续页 URL（挂牌/成交通用 pg{N}c{ID}，无点击）。

    只收录携带同一小区 token 的链接并 urljoin 还原绝对地址。与 MVP 同源。
    """
    token = _token_from_url(page_url)
    if token is None:
        return []
    page_urls: list[str] = []
    seen_pages: set[int] = set()
    for match in re.finditer(r'href="([^"]*pg(\d+)[^"]*)"', first_page_html or ""):
        href = urljoin(page_url, unescape(match.group(1)))
        path = urlparse(href).path
        num_m = re.search(r"pg(\d+)", path)
        if num_m is None or token not in path:
            continue
        page_no = int(num_m.group(1))
        if page_no in seen_pages or page_no < 2 or page_no > max_pages:
            continue
        seen_pages.add(page_no)
        page_urls.append(href)
    return page_urls


async def _collect_deals_by_url(
    page,
    *,
    community_name: str,
    deal_page_url: str,
    max_pages: int = 50,
) -> list[dict]:
    """直达成交页采集真实成交（全量，键名对齐入库层 {area,date,total_price,price}）。

    成交分页优先 URL 直达（原生 pg 链接）；无链接但声明多页时回退点击
    a[data-page]。与 MVP collect_deals 同源。
    """
    deals: list[dict] = []
    await page.get(deal_page_url)
    await page
    await asyncio.sleep(3)
    first_html = await wait_and_reload_after_block(
        page, detect_block, f"小区成交页[{community_name}]"
    )
    await _dump(page, "lj_deal_by_url_p1")

    raw = parsers.parse_deal_records(first_html)
    deals = [
        {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
        for r in raw if r[0] is not None and r[3] is not None
    ]
    log.info("[成交] %s 第 1 页真实成交 %d 条", community_name, len(deals))

    page_urls = _build_pagination_urls(deal_page_url, first_html, max_pages)
    total_pages = parsers.parse_deal_total_pages(first_html)
    if page_urls:
        log.info("[成交] 另有 %d 页待采（URL 直达）", len(page_urls))
        for page_no, page_url in enumerate(page_urls, start=2):
            await page.get(page_url)
            await page
            await asyncio.sleep(2)
            page_html = await wait_and_reload_after_block(
                page, detect_block, f"成交翻页第 {page_no} 页"
            )
            await _dump(page, f"lj_deal_by_url_p{page_no}")
            page_raw = parsers.parse_deal_records(page_html)
            page_deals = [
                {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
                for r in page_raw if r[0] is not None and r[3] is not None
            ]
            deals.extend(page_deals)
            if not page_deals:
                log.info("[成交] 第 %d 页无成交记录，停止翻页", page_no)
                break
            log.info("[成交] 第 %d 页解析 %d 条，累计 %d 条", page_no, len(page_deals), len(deals))
    elif total_pages > 1:
        log.info("[成交] 页面无 URL 分页链接（totalPage=%d），回退点击翻页", total_pages)
        for page_no in range(2, total_pages + 1):
            page_html = await _click_deal_page_number(page, page_no)
            if page_html is None:
                log.warning("[成交] 第 %d 页无法翻页，停止", page_no)
                break
            page_html = await wait_and_reload_after_block(
                page, detect_block, f"成交点击第 {page_no} 页"
            )
            await _dump(page, f"lj_deal_by_url_p{page_no}")
            page_raw = parsers.parse_deal_records(page_html)
            page_deals = [
                {"area": r[0], "date": r[1], "total_price": r[2], "price": r[3]}
                for r in page_raw if r[0] is not None and r[3] is not None
            ]
            deals.extend(page_deals)
            if not page_deals:
                break
    log.info("[成交汇总] %s：真实成交 %d 条", community_name, len(deals))
    return deals


async def collect_listing_by_url(
    page,
    *,
    community_name: str,
    listing_page_url: str,
    deal_page_url: Optional[str] = None,
    request_id: Optional[str] = None,
    max_pages: int = 50,
) -> PlatformResult:
    """URL 直达采集小区在售与真实成交（非交互，与 MVP 模板等价）。

    在售：直达挂牌页 → 风控 → is_no_result 空态 → 归属 → 无点击翻页。
    成交：deal_page_url 提供时独立直达采集全量真实成交（挂牌空态也采）。
    只回传原始明细（listing_snapshots/deal_records）；成交面积收敛与
    估价由算法层（aggregation → app.algorithm.deal_screening）接管。
    """
    start = time.time()
    try:
        await page.get(listing_page_url)
        await page
        await asyncio.sleep(3)
        html = await wait_and_reload_after_block(
            page, detect_block, f"小区挂牌页[{community_name}]"
        )
        await _dump(page, "lj_listing_by_url_p1")

        if is_no_result(html):
            log.info("[空态] %s 在链家在售 0 条（跳过推荐位）", community_name)
            listing_snapshots = []
            empty_listing = True
        else:
            listing_snapshots = parsers.parse_listing_snapshots(html, base_url=page.target.url)
            empty_listing = False
            if listing_snapshots and not has_matching_community_snapshots(listing_snapshots, community_name):
                log.warning(
                    "[归属不符] %s：%s 页面快照与目标小区不匹配（%d 条全部弃用）",
                    community_name, listing_page_url, len(listing_snapshots),
                )
                listing_snapshots = []

        if listing_snapshots:
            page_urls = _build_pagination_urls(listing_page_url, html, max_pages)
            if page_urls:
                log.info("[翻页] 在售第 1 页 %d 条；另有 %d 页待采", len(listing_snapshots), len(page_urls))
            for page_no, page_url in enumerate(page_urls, start=2):
                await page.get(page_url)
                await page
                await asyncio.sleep(2)
                page_html = await wait_and_reload_after_block(
                    page, detect_block, f"在售翻页第 {page_no} 页"
                )
                await _dump(page, f"lj_listing_by_url_p{page_no}")
                page_snapshots = parsers.parse_listing_snapshots(page_html, base_url=page.target.url)
                listing_snapshots.extend(page_snapshots)
                if not page_snapshots:
                    log.info("[翻页] 在售第 %d 页无房源，停止翻页", page_no)
                    break

        deals: list[dict] = []
        if deal_page_url:
            deals = await _collect_deals_by_url(
                page, community_name=community_name,
                deal_page_url=deal_page_url, max_pages=max_pages,
            )

        # RPA 语义收窄：成交只回传原始明细，面积收敛由算法层接管
        status = PlatformResultStatus.SUCCESS if (listing_snapshots or deals) else PlatformResultStatus.NO_DATA
        log.info(
            "[采集汇总] %s：在售 %d 条，成交 %d 条%s",
            community_name, len(listing_snapshots), len(deals),
            "" if not empty_listing else "（挂牌空态，成交独立采集）",
        )
        return PlatformResult(
            name="链家",
            status=status,
            quote_prices=[],
            deal_prices=[],
            deal_records=deals,
            deal_source="成交记录" if deals else "无",
            request_id=request_id,
            listing_page_url=listing_page_url,
            deal_page_url=deal_page_url,
            elapsed_seconds=round(time.time() - start, 2),
            listing_snapshots=listing_snapshots,
        )
    except Exception as exc:
        log.exception("链家 URL 直达采集异常：%s", exc)
        return PlatformResult(
            name="链家",
            status=PlatformResultStatus.ERROR,
            reason=str(exc),
            listing_page_url=listing_page_url,
            deal_page_url=deal_page_url,
            request_id=request_id,
            elapsed_seconds=round(time.time() - start, 2),
        )
