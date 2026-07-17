# -*- coding: utf-8 -*-
"""HTML 解析器。"""

import json
import re
from typing import Iterable, List, Optional

from app.core.models import DealRecord, ListingSnapshot

try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


_UNIT_PRICE_RE = re.compile(r"[\d,]+(?:\.\d+)?\s*元\s*/?\s*(?:平米|平|㎡|m²)")
_AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:平米|㎡|m²)")
_LAYOUT_RE = re.compile(r"(\d+室\d+厅)")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _to_float(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", text)
    return float(m.group(0).replace(",", "")) if m else None


def _find_unit_price_text(text: str) -> Optional[str]:
    m = _UNIT_PRICE_RE.search(text or "")
    return m.group(0) if m else None


def _extract_area(text: str) -> Optional[float]:
    m = _AREA_RE.search(text or "")
    return float(m.group(1)) if m else None


def _extract_layout(text: str) -> Optional[str]:
    m = _LAYOUT_RE.search(text or "")
    return m.group(1) if m else None


def _append_record(
    records: List[DealRecord],
    seen: set,
    area: Optional[float],
    unit_price: Optional[float],
):
    if area is None or unit_price is None:
        return

    key = (round(area, 2), round(unit_price, 2))
    if key in seen:
        return

    seen.add(key)
    records.append(DealRecord(area=area, unit_price=unit_price))


def _parse_embedded_sold_records(html: str) -> List[DealRecord]:
    """从小区详情页脚本中的 sold 数组解析成交记录。"""
    m = re.search(
        r'"sold"\s*:\s*(\[[\s\S]*?\])\s*,\s*"soldUrl"\s*:\s*"[^"]*"',
        html or "",
    )
    if not m:
        return []

    try:
        items = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    records: List[DealRecord] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue

        area = item.get("area") or item.get("buildSize")
        unit_price = item.get("unitPrice")
        try:
            area_value = float(area) if area is not None else None
            unit_price_value = (
                float(str(unit_price).replace(",", ""))
                if unit_price is not None
                else None
            )
        except (TypeError, ValueError):
            continue

        _append_record(records, seen, area_value, unit_price_value)

    return records


def _select_listing_items(soup: "BeautifulSoup"):
    """只抓主结果列表，过滤猜你喜欢等推荐区块。"""
    items = soup.select('ul.sellListContent[log-mod="list"] > li')
    if items:
        return items
    return soup.select("ul.sellListContent > li")


def parse_listing_records(html: str) -> List[tuple[str, float]]:
    """解析搜索结果页主结果列表中的房源 ID 和单价。"""
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        records: List[tuple[str, float]] = []
        for li in _select_listing_items(soup):
            cls = " ".join(li.get("class", []))
            if "goodhouse" in cls:
                continue

            unit_el = li.select_one("div.unitPrice")
            if not unit_el:
                continue

            house_id = unit_el.get("data-hid")
            price_text = unit_el.get_text(" ", strip=True)
            price = _to_float(price_text)
            if house_id and price:
                records.append((house_id, price))
        return records

    return _parse_listing_records_regex(html)


def parse_listing_snapshots(html: str) -> List[ListingSnapshot]:
    """解析搜索结果页主结果列表中的房源摘要。"""
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        snapshots: list[ListingSnapshot] = []
        for li in _select_listing_items(soup):
            cls = " ".join(li.get("class", []))
            if "goodhouse" in cls:
                continue

            unit_el = li.select_one("div.unitPrice")
            if not unit_el:
                continue

            house_id = unit_el.get("data-hid")
            if not house_id:
                continue

            community_name = None
            community_el = li.select_one(".positionInfo a")
            if community_el:
                community_name = _normalize_text(community_el.get_text(" ", strip=True))

            house_info_text = ""
            house_info_el = li.select_one(".houseInfo")
            if house_info_el:
                house_info_text = _normalize_text(house_info_el.get_text(" ", strip=True))

            total_price = None
            total_price_el = li.select_one(".totalPrice span")
            if total_price_el:
                total_price = _to_float(total_price_el.get_text(" ", strip=True))

            unit_price = _to_float(unit_el.get_text(" ", strip=True))
            snapshots.append(
                ListingSnapshot(
                    house_id=house_id,
                    community_name=community_name,
                    area=_extract_area(house_info_text),
                    layout=_extract_layout(house_info_text),
                    unit_price=unit_price,
                    total_price=total_price,
                )
            )
        return snapshots

    return []


def _parse_listing_records_regex(html: str) -> List[tuple[str, float]]:
    container_match = re.search(
        r'<ul[^>]*class="[^"]*sellListContent[^"]*"[^>]*log-mod="list"[^>]*>([\s\S]*?)</ul>',
        html,
        re.S,
    )
    container = container_match.group(1) if container_match else html

    records: List[tuple[str, float]] = []
    for m in re.finditer(r'<li[^>]*class="[^"]*clear[^"]*"[^>]*>(.*?)</li>', container, re.S):
        block = m.group(0)
        if "goodhouse" in block:
            continue

        unit = re.search(
            r'<div class="unitPrice"[^>]*data-hid="([^"]+)"[^>]*>\s*<span>([^<]*)</span>',
            block,
        )
        if not unit:
            continue

        price = _to_float(unit.group(2))
        if price:
            records.append((unit.group(1), price))
    return records


def parse_listing_prices(html: str) -> List[float]:
    """解析搜索结果页主结果列表中的在售单价。"""
    return [price for _, price in parse_listing_records(html)]


def find_detail_link(html: str) -> Optional[str]:
    """提取搜索结果页里的小区详情链接。

    三层匹配策略（不依赖 class/href 属性顺序，不依赖特定城市子域名）：
    1. BeautifulSoup CSS 选择器 a.agentCardResblockLink（最可靠）
    2. 正则匹配整个 <a> 标签再单独提取 href（兜底无 bs4 场景）
    3. 通过"查看小区详情"文本匹配，支持任意城市子域名（\\w+.ke.com）
    """
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("a.agentCardResblockLink")
        if el and el.get("href"):
            return el["href"]

    m = re.search(
        r'<a[^>]*class="[^"]*agentCardResblockLink[^"]*"[^>]*>',
        html or "",
    )
    if m:
        href_m = re.search(r'href="([^"]+)"', m.group(0))
        if href_m:
            return href_m.group(1)

    m = re.search(
        r'<a[^>]*href="(https?://[\w.]+\.ke\.com/xiaoqu/\d+/)"[^>]*>查看小区详情',
        html or "",
    )
    return m.group(1) if m else None


def find_sold_list_url(html: str) -> Optional[str]:
    """提取小区详情页里的成交列表链接。"""
    m = re.search(r'"soldUrl"\s*:\s*"([^"]+)"', html or "")
    if not m:
        return None
    return m.group(1).replace("\\/", "/")


def parse_community_avg_price(html: str) -> Optional[float]:
    """解析小区详情页中的参考均价。"""
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.select(".xiaoquUnitPrice, .xiaoquPrice, .junjia"):
            text = _find_unit_price_text(el.get_text())
            if text:
                return _to_float(text)

    text = _find_unit_price_text(html)
    return _to_float(text) if text else None


def _iter_deal_texts(html: str) -> Iterable[str]:
    if _HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.select("li, div"):
            text = _normalize_text(el.get_text(separator=" ", strip=True))
            if 8 <= len(text) <= 240:
                yield text
        return

    for m in re.finditer(r"<(?:li|div)[^>]*>(.*?)</(?:li|div)>", html, re.S):
        text = _normalize_text(re.sub(r"<[^>]+>", " ", m.group(1)))
        if 8 <= len(text) <= 240:
            yield text


def parse_deal_records(html: str) -> List[DealRecord]:
    """解析成交案例中的面积与单价。"""
    records: List[DealRecord] = []
    seen = set()

    for text in _iter_deal_texts(html):
        if "参考均价" in text:
            continue

        area = _extract_area(text)
        price_text = _find_unit_price_text(text)
        unit_price = _to_float(price_text) if price_text else None
        _append_record(records, seen, area, unit_price)

    for record in _parse_embedded_sold_records(html):
        _append_record(records, seen, record.area, record.unit_price)

    return records


def parse_deal_prices(html: str) -> List[float]:
    return [record.unit_price for record in parse_deal_records(html)]


def filter_deal_prices_by_area(
    records: List[DealRecord],
    area_min: float,
    area_max: float,
) -> List[float]:
    """按请求面积严格区间过滤成交单价（与链家/房天下统一口径）。"""
    return [
        record.unit_price
        for record in records
        if record.area is not None and area_min <= record.area <= area_max
    ]


def parse_area_segments(html: str) -> List[tuple]:
    """从结果页面积筛选区动态读取面积档位。

    贝壳面积筛选区 DOM：
      <dl class="hide hasmore">
        <dt title="深圳建筑面积在售二手房">建筑面积</dt>
        <dd>
          <a href=".../a1/"><span class="checkbox"></span><span class="name">50㎡以下</span></a>
          <a href=".../a3/"><span class="checkbox"></span><span class="name">70-90㎡</span></a>
          <a href=".../a8/"><span class="checkbox"></span><span class="name">200㎡以上</span></a>
          <span class="customFilter" data-role="area">...</span>  ← 自定义输入框，面积区结束标志

    档位因城市而异（深圳 8 档、佛山 7 档，数值不同），必须从 HTML 动态读取。

    Returns:
        [(text, min, max), ...]，按页面顺序。
        - "50㎡以下"  → (0, 50)
        - "70-90㎡"   → (70, 90)
        - "200㎡以上" → (200, inf)
        自定义输入框跳过。
    """
    # 贝壳面积区以 data-role="area" 的 customFilter 结束
    end_marker = re.search(r'data-role=["\']area["\']', html)
    if not end_marker:
        return []
    end = end_marker.start()
    # 从结束标志往前找"建筑面积"标题，确定面积区起点
    title_match = re.search(r'建筑面积', html[:end])
    if not title_match:
        return []
    start = title_match.end()
    chunk = html[start:end]

    segments = []
    # 匹配 <a><span class="name">XX㎡</span></a>
    for m in re.finditer(
        r'<a[^>]*>.*?<span[^>]*class="[^"]*name[^"]*"[^>]*>([^<]*㎡[^<]*)</span>',
        chunk, re.S,
    ):
        text = m.group(1).strip()
        parsed = _parse_segment_text(text)
        if parsed is not None:
            segments.append((text, parsed[0], parsed[1]))
    return segments


def _parse_segment_text(text: str):
    """解析单个面积档位文本 → (min, max)。

    贝壳用"㎡"单位：
    - "50㎡以下"  → (0, 50)
    - "70-90㎡"   → (70, 90)
    - "200㎡以上" → (200, inf)
    """
    text = text.strip()

    # XX㎡以下
    m = re.match(r'(\d+)\s*㎡?\s*以下', text)
    if m:
        return (0.0, float(m.group(1)))

    # XX-YY㎡
    m = re.match(r'(\d+)\s*[-~]\s*(\d+)\s*㎡?', text)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    # XX㎡以上
    m = re.match(r'(\d+)\s*㎡?\s*以上', text)
    if m:
        return (float(m.group(1)), float('inf'))

    return None
