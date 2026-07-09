# -*- coding: utf-8 -*-
"""贝壳 HTML 解析器（纯函数）。

基于实测 DOM 结构：
  真实房源: <li class="clear">
  广告:     <li class="list_goodhouse_daoliu ...">  → 过滤
  猜你喜欢: sellListContent 之外的区块 → 不解析
  单价:     div.unitPrice span       → "84,547元/平"
  详情链接: a.agentCardResblockLink  → href 指向 /xiaoqu/{id}/

详情页：
  小区均价: 含"参考均价""元/㎡"文本
  成交记录: 含"元/平"的条目
"""
import re
from typing import List, Optional

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


def _to_float(text: str) -> Optional[float]:
    """'84,547元/平' → 84547.0。"""
    if not text:
        return None
    m = re.search(r"[\d,]+\.?\d*", text)
    return float(m.group(0).replace(",", "")) if m else None


def _find_price_text(text: str) -> Optional[str]:
    """找 'XX,XXX元/平' 或 '元/㎡' 格式的单价串。"""
    m = re.search(r"[\d,]+\.?\d*\s*元/[平㎡]", text or "")
    return m.group(0) if m else None


def parse_listing_prices(html: str) -> List[float]:
    """从搜索结果页解析在售房源单价列表。自动过滤广告。

    只提取单价（XXX元/平），其他不要。
    """
    if not _HAS_BS4:
        return _parse_listing_prices_regex(html)
    soup = BeautifulSoup(html, "html.parser")
    prices = []
    for li in soup.select("ul.sellListContent > li"):
        cls = " ".join(li.get("class", []))
        if "goodhouse" in cls:  # 广告过滤
            continue
        unit_el = li.select_one("div.unitPrice span")
        if unit_el:
            p = _to_float(unit_el.get_text())
            if p:
                prices.append(p)
    return prices


def _parse_listing_prices_regex(html: str) -> List[float]:
    """无 bs4 时的正则兜底。"""
    prices = []
    for m in re.finditer(r'<li[^>]*class="clear"[^>]*>(.*?)</li>', html, re.S):
        block = m.group(0)
        unit = re.search(r'<div class="unitPrice"[^>]*>\s*<span>([^<]*)</span>', block)
        if unit:
            p = _to_float(unit.group(1))
            if p:
                prices.append(p)
    return prices


def find_detail_link(html: str) -> Optional[str]:
    """从搜索结果页提取'查看小区详情'链接。"""
    m = re.search(r'<a[^>]*class="[^"]*agentCardResblockLink[^"]*"[^>]*href="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'<a[^>]*href="(https?://sz\.ke\.com/xiaoqu/\d+/)"[^>]*>查看小区详情', html)
    return m.group(1) if m else None


def parse_community_avg_price(html: str) -> Optional[float]:
    """从详情页解析小区均价（参考均价 XX,XXX元/㎡）。"""
    # 优先用 bs4 精确定位
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.select(".xiaoquUnitPrice, .xiaoquPrice, .junjia"):
            t = _find_price_text(el.get_text())
            if t:
                return _to_float(t)
    # 正则兜底：找第一个"数字元/㎡"
    m = re.search(r"([\d,]+\.?\d*)\s*元/[平㎡]", html)
    return _to_float(m.group(0)) if m else None


def parse_deal_prices(html: str) -> List[float]:
    """从详情页解析成交记录单价列表。用稳健文本匹配。"""
    prices = []
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for li in soup.select("li"):
            text = li.get_text(separator=" ", strip=True)
            p_text = _find_price_text(text)
            if p_text:
                p = _to_float(p_text)
                if p:
                    prices.append(p)
    return prices
