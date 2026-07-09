# -*- coding: utf-8 -*-
"""贝壳 HTML 解析器（纯函数）。基于实测 DOM 结构。

DOM 结构(实测)：
  真实房源: <li class="clear">
  广告:     <li class="list_goodhouse_daoliu ...">
  单价:     div.unitPrice span       → "84,547元/平"
  总价:     div.totalPrice span      → "720"
  信息:     div.houseInfo            → "... | 85.16平米 | ..."
"""
import re
from typing import List, Dict, Any, Optional

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


def _to_float(text: str) -> Optional[float]:
    """'84,547元/平' → 84547.0；提取失败返回 None。"""
    if not text:
        return None
    m = re.search(r"[\d,]+\.?\d*", text)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def _extract_area(house_info_text: str) -> Optional[float]:
    """'... | 85.16平米 | ...' → 85.16。"""
    if not house_info_text:
        return None
    m = re.search(r"([\d.]+)\s*平米", house_info_text)
    return float(m.group(1)) if m else None


def is_ad(li_html: str) -> bool:
    """判断是否广告/导流位。"""
    return "list_goodhouse_daoliu" in li_html or "goodhouse" in li_html.lower()


def is_guess_you_like_boundary(text: str) -> bool:
    """判断是否「猜你喜欢」分界（sellListContent 之外，到此结束）。"""
    return "猜你喜欢" in text or "猜您喜欢" in text


def parse_listings(html: str) -> List[Dict[str, Any]]:
    """从搜索结果页 HTML 解析在售房源。自动过滤广告。

    Returns: [{"unit_price", "total_price", "area"}, ...]
    """
    if _HAS_BS4:
        return _parse_with_bs4(html)
    return _parse_with_regex(html)


def _parse_with_bs4(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for li in soup.select("ul.sellListContent > li"):
        cls = " ".join(li.get("class", []))
        if is_ad(cls):
            continue
        unit_el = li.select_one("div.unitPrice span")
        total_el = li.select_one("div.totalPrice span")
        info_el = li.select_one("div.houseInfo")
        results.append({
            "unit_price": _to_float(unit_el.get_text() if unit_el else None),
            "total_price": _to_float(total_el.get_text() if total_el else None),
            "area": _extract_area(info_el.get_text() if info_el else None),
        })
    return results


def _parse_with_regex(html: str) -> List[Dict[str, Any]]:
    """无 bs4 时的正则兜底解析。"""
    results = []
    for m in re.finditer(r'<li[^>]*class="clear"[^>]*>(.*?)</li>', html, re.S):
        block = m.group(0)
        unit = re.search(r'<div class="unitPrice"[^>]*>\s*<span>([^<]*)</span>', block)
        total = re.search(r'<div class="totalPrice[^"]*"[^>]*>\s*<span[^>]*>([^<]*)</span>', block)
        info = re.search(r'<div class="houseInfo"[^>]*>(.*?)</div>', block, re.S)
        results.append({
            "unit_price": _to_float(unit.group(1) if unit else None),
            "total_price": _to_float(total.group(1) if total else None),
            "area": _extract_area(re.sub(r"<[^>]+>", " ", info.group(1)) if info else None),
        })
    return results


def find_detail_link(html: str) -> Optional[str]:
    """从搜索结果页提取「查看小区详情」链接。"""
    m = re.search(r'<a[^>]*class="[^"]*agentCardResblockLink[^"]*"[^>]*href="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'<a[^>]*href="(https?://sz\.ke\.com/xiaoqu/\d+/)"[^>]*>查看小区详情', html)
    return m.group(1) if m else None


def parse_deals(html: str) -> List[Dict[str, Any]]:
    """从详情页解析成交记录。用稳健文本匹配，不依赖固定 class。

    策略：找所有「XX,XXX元/平」附近带面积的条目。
    """
    results = []
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for li in soup.select("li"):
            text = li.get_text(separator=" ", strip=True)
            if "元/平" not in text and "元/㎡" not in text:
                continue
            unit = _to_float(_find_price_text(text))
            area = _extract_area(text)
            if unit or area:
                results.append({"unit_price": unit, "area": area})
    else:
        for m in re.finditer(r"<li[^>]*>(.*?)</li>", html, re.S):
            text = re.sub(r"<[^>]+>", " ", m.group(1))
            if "元/平" not in text and "元/㎡" not in text:
                continue
            results.append({"unit_price": _to_float(_find_price_text(text)),
                            "area": _extract_area(text)})
    return results


def _find_price_text(text: str) -> Optional[str]:
    """从文本中找 'XX,XXX元/平' 格式的单价串。"""
    m = re.search(r"[\d,]+\.?\d*\s*元/[平㎡]", text)
    return m.group(0) if m else None


def parse_community_avg_price(html: str) -> Optional[float]:
    """从详情页解析小区均价（'XX,XXX元/㎡' 或 参考均价）。"""
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.select(".xiaoquPrice, .junjia, [class*='unitPrice']"):
            t = _find_price_text(el.get_text() or "")
            if t:
                return _to_float(t)
    m = re.search(r"([\d,]+\.?\d*)\s*元/[平㎡]", html)
    return _to_float(m.group(0)) if m else None
