# -*- coding: utf-8 -*-
"""乐有家平台 HTML 解析器。

从结果页提取在售房源快照与小区均价。乐有家无成交记录，
业务上用小区均价当作 deal_prices 的替代（见 adapter 注释）。
"""

import re
from typing import Optional

from app.core.models import ListingSnapshot


def _normalize_text(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_listing_snapshots(html: str) -> list:
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


def parse_community_avg_price(html: str) -> Optional[float]:
    """从结果页社区信息卡提取小区均价。

    DOM: <em class="label">小区均价</em><em class="txt">54386元/㎡</em>

    乐有家无成交记录，业务上用小区均价顶替 deal_prices。
    """
    m = re.search(r"小区均价</em>\s*<em\s[^>]*>\s*([\d,]+)\s*元", html)
    return float(m.group(1).replace(",", "")) if m else None


def parse_total_pages(html: str) -> int:
    """解析总页数。尾页链接: <a title="N">尾页</a>"""
    m = re.search(r'<a[^>]*title="(\d+)"[^>]*>尾页</a>', html or "")
    return int(m.group(1)) if m else 1


def parse_current_page(html: str) -> int:
    m = re.search(r'<a[^>]*class="on"[^>]*href="[^"]*">(\d+)</a>', html or "")
    return int(m.group(1)) if m else 1


def parse_area_segments(html: str) -> list:
    """从结果页面积筛选区动态读取面积档位。

    乐有家面积筛选区 DOM：
      <div class="selected-index multi-select hasmore">
        <span class="c333 tit">面积</span>
        <a href=".../esf/m1/" class="xx5"><span class="name">30㎡以下</span></a>
        <a href=".../esf/m4/" class="xx5"><span class="name">80-100㎡</span></a>
        <a href=".../esf/m9/" class="xx5"><span class="name">200㎡以上</span></a>
        <span class="selected-input">...</span>  ← 自定义输入框，跳过

    档位因城市而异，必须从 HTML 动态读取。
    注意：乐有家用"㎡"单位，档位文本在 <span class="name"> 里。

    Returns:
        [(text, min, max), ...]，按页面顺序。
        - "30㎡以下"   → (0, 30)
        - "80-100㎡"   → (80, 100)
        - "200㎡以上"  → (200, inf)
        自定义输入框跳过。
    """
    # 定位面积筛选区：c333 tit">面积 到下一个 selected-index（户型/类型等）
    title_match = re.search(r'c333[^>]*tit[^>]*>面积', html)
    if not title_match:
        return []
    start = title_match.end()
    # 找下一个 selected-index（户型等），截断面积区
    next_section = re.search(r'selected-index multi-select', html[start:])
    end = start + next_section.start() if next_section else start + 5000
    chunk = html[start:end]

    segments = []
    # 匹配 <a class="xx5"> 里的 <span class="name"> 文本
    for m in re.finditer(
        r'<a[^>]*class="xx\d[^"]*"[^>]*>(.*?)</a>',
        chunk, re.S,
    ):
        inner = m.group(1)
        # 提取 <span class="name"> 的文本
        text_m = re.search(r'<span\s[^>]*class="[^"]*name[^"]*"[^>]*>([^<]+)</span>', inner)
        if not text_m:
            continue
        text = text_m.group(1).strip()
        parsed = _parse_segment_text(text)
        if parsed is not None:
            segments.append((text, parsed[0], parsed[1]))
    return segments


def _parse_segment_text(text: str):
    """解析单个面积档位文本 → (min, max)。

    乐有家用"㎡"单位：
    - "30㎡以下"   → (0, 30)
    - "80-100㎡"   → (80, 100)
    - "200㎡以上"  → (200, inf)
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
