# -*- coding: utf-8 -*-
"""链家平台 HTML 解析器。

链家是贝壳子公司，DOM 与贝壳高度一致（同代码库），解析逻辑相似但独立实现。
成交筛选用严格面积区间 + 近半年（贝壳用 ±20% 容差，两者口径不同，不混用）。
"""

import re
from datetime import datetime, timedelta

from app.core.models import ListingSnapshot


def parse_listing_snapshots(html: str) -> list:
    """从结果页主结果列表提取在售房源快照。

    DOM（和贝壳一致）：
      <ul class="sellListContent">
        <li class="clear">
          <div class="positionInfo">...<a>绿景虹湾</a>...</div>
          <div class="houseInfo">3室2厅 | 87.57平米 | 东 | ...</div>
          <div class="unitPrice" data-price="86788"><span>86,788元/平</span></div>
        </li>

    边界：截断到"猜你喜欢"之前，排除推荐位（和安居客 list-guess-title 同类）。
    """
    cut = html.find("猜你喜欢")
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(r'<li class="clear[^"]*"[^>]*>(.*?)(?=<li class="clear|$)', main_html, re.S):
        chunk = block.group(1)
        if "goodhouse" in chunk:
            continue

        # 小区名
        name_m = re.search(r'<div class="positionInfo">.*?<a[^>]*>([^<]+)</a>', chunk, re.S)
        community_name = name_m.group(1).strip() if name_m else None

        # 户型+面积
        info_m = re.search(r'<div class="houseInfo">.*?>(.*?)</div>', chunk, re.S)
        layout = None
        area = None
        if info_m:
            info_text = re.sub(r'<[^>]+>', '', info_m.group(1))
            layout_m = re.search(r'(\d+室\d+厅)', info_text)
            if layout_m:
                layout = layout_m.group(1)
            area_m = re.search(r'([\d.]+)\s*平米', info_text)
            if area_m:
                area = float(area_m.group(1))

        # 总价(万)
        total_m = re.search(r'class="totalPrice[^"]*"[^>]*>.*?<span[^>]*>([\d.]+)</span>', chunk, re.S)
        total_price = float(total_m.group(1)) if total_m else None

        # 单价（优先用 data-price 属性）
        unit_m = re.search(r'class="unitPrice"[^>]*data-price="([\d]+)"', chunk)
        if not unit_m:
            unit_m = re.search(r'class="unitPrice"[^>]*>.*?<span>([\d,]+)\s*元', chunk, re.S)
        unit_price = float(unit_m.group(1).replace(",", "")) if unit_m else None

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


def parse_listing_total_pages(html: str) -> int:
    """从在售结果页分页区解析总页数。

    DOM（和贝壳一致）：
      page-data="{&quot;totalPage&quot;:3,&quot;curPage&quot;:1}"
    """
    m = re.search(r'totalPage&quot;:(\d+)', html or "")
    return int(m.group(1)) if m else 1


def parse_deal_records(html: str) -> list:
    """从成交页 ul.listContent 解析成交记录。

    DOM（和贝壳成交页一致）：
      <ul class="listContent"><li>
        <div class="title"><a>绿景虹湾 3室1厅 75.14平米</a></div>
        <div class="dealDate">2026.05.06</div>
        <div class="totalPrice"><span class="number">558</span>万</div>
        <div class="unitPrice"><span class="number">74262</span>元/平</div>

    返回 [(面积, 日期, 总价万, 单价), ...]，面积从 title 提取。
    日期格式统一转为 YYYY-MM-DD（原始是 2026.05.06）。
    """
    records = []
    for m in re.finditer(r'<li[^>]*>(.*?)(?=<li[^>]*>|</ul>)', html, re.S):
        chunk = m.group(1)
        if "dealDate" not in chunk:
            continue

        title_m = re.search(r'<div class="title">.*?>(.*?)</a>', chunk, re.S)
        area = None
        if title_m:
            area_m = re.search(r'([\d.]+)\s*平米', title_m.group(1))
            if area_m:
                area = float(area_m.group(1))

        date_m = re.search(r'<div class="dealDate">([\d.]+)</div>', chunk)
        date_str = date_m.group(1).replace(".", "-") if date_m else None

        total_m = re.search(r'class="totalPrice">.*?class="number">([\d.]+)', chunk, re.S)
        total_price = float(total_m.group(1)) if total_m else None

        unit_m = re.search(r'class="unitPrice">.*?class="number">([\d,]+)', chunk, re.S)
        unit_price = float(unit_m.group(1).replace(",", "")) if unit_m else None

        if area is None and unit_price is None:
            continue

        records.append((area, date_str, total_price, unit_price))
    return records


def parse_deal_total_pages(html: str) -> int:
    """从成交页分页区解析总页数。"""
    m = re.search(r'totalPage&quot;:(\d+)', html or "")
    return int(m.group(1)) if m else 1


def filter_deal_records(records: list, area_min: float, area_max: float, months: int = 6) -> list:
    """过滤成交记录：严格面积区间 + 近 months 个月。

    链家规则：严格面积区间，日期 >= 今天往前 months 个月。
    贝壳/链家/房天下已统一为严格面积区间口径（贝壳成交少，不额外筛日期）。
    """
    cutoff = datetime.now() - timedelta(days=30 * months)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    filtered = []
    for area, date_str, total, price in records:
        if area is not None and not (area_min <= area <= area_max):
            continue
        if date_str and date_str < cutoff_str:
            continue
        filtered.append((area, date_str, total, price))
    return filtered


def parse_area_segments(html: str) -> list:
    """从结果页面积筛选区动态读取面积档位。

    链家 DOM 与贝壳一致（同代码库），唯一差异：标题是"面积"（贝壳是"建筑面积"）。
      <dl class="hide hasmore">
        <dt>面积</dt>
        <dd>
          <a href=".../a1/"><span class="checkbox"></span><span class="name">50㎡以下</span></a>
          <a href=".../a3/"><span class="checkbox"></span><span class="name">70-90㎡</span></a>
          <span class="customFilter" data-role="area">...</span>  ← 面积区结束标志

    档位因城市而异（深圳 8 档、佛山 7 档），必须从 HTML 动态读取。

    Returns:
        [(text, min, max), ...]
    """
    # 以 data-role="area" 的 customFilter 为面积区结束标志
    end_marker = re.search(r'data-role=["\']area["\']', html)
    if not end_marker:
        return []
    end = end_marker.start()
    # 链家面积标题是"面积"，从结束标志往前找
    title_match = None
    for m in re.finditer(r'>面积<', html[:end]):
        title_match = m  # 取最后一个匹配（确保是最近的面积标题）
    if not title_match:
        return []
    start = title_match.end()
    chunk = html[start:end]

    segments = []
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

    - "50㎡以下"  → (0, 50)
    - "70-90㎡"   → (70, 90)
    - "200㎡以上" → (200, inf)
    """
    text = text.strip()

    m = re.match(r'(\d+)\s*㎡?\s*以下', text)
    if m:
        return (0.0, float(m.group(1)))

    m = re.match(r'(\d+)\s*[-~]\s*(\d+)\s*㎡?', text)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    m = re.match(r'(\d+)\s*㎡?\s*以上', text)
    if m:
        return (float(m.group(1)), float('inf'))

    return None
