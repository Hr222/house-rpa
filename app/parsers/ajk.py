# -*- coding: utf-8 -*-
"""安居客平台 HTML 解析器。

从结果页提取在售房源快照与挂牌均价。安居客无成交记录，
业务上把挂牌均价当作 deal_prices 的替代（见 adapter 注释）。
"""

import re
from typing import Optional

from app.core.models import ListingSnapshot


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

        # 营销标题：h3 的 title 属性（文本内有 <i> 高亮，title 属性是纯文本）
        title = None
        title_m = re.search(r'<h3[^>]*title="([^"]+)"', chunk)
        if title_m:
            title = title_m.group(1).strip()

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
                title=title,
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


def parse_area_segments(html: str) -> list:
    """从结果页面积筛选区动态读取面积档位。

    安居客面积筛选区 DOM：
      <span class="filter-title filter-title-line">面积</span>
      <section class="filter-content"><ul class="line">
        <li class="line-item line-item-active"><a>不限</a></li>
        <li class="line-item"><a href=".../a11950/">50㎡以下</a></li>
        <li class="line-item"><a href=".../a11954/">80-90㎡</a></li>
        <li class="line-item"><a href=".../a11959/">190㎡以上</a></li>
        <li class="line-item line-item-input">...</li>  ← 自定义输入框，跳过

    档位因城市而异（深圳 9 档、汕尾 8 档，数值不同），
    必须从 HTML 动态读取，不能硬编码。

    Returns:
        [(text, min, max), ...]，按页面顺序。
        - "50㎡以下"  → (0, 50)
        - "80-90㎡"   → (80, 90)
        - "190㎡以上" → (190, float('inf'))
        "不限"和自定义输入框跳过。
    """
    # 定位面积筛选区：从"面积"标题到下一个 filter-wrap（房型/朝向等）
    title_match = re.search(r'filter-title-line[^>]*>面积', html)
    if not title_match:
        return []
    start = title_match.end()
    # 找下一个 filter-title（房型、朝向等），截断面积区
    next_filter = re.search(r'filter-title-line', html[start:])
    end = start + next_filter.start() if next_filter else start + 5000
    chunk = html[start:end]

    segments = []
    # 匹配 line-item（排除 line-item-input 自定义输入框）
    for m in re.finditer(
        r'<li[^>]*class="line-item(?!-input)(?:"|\s[^"]*")[^>]*>(.*?)</li>',
        chunk, re.S,
    ):
        inner = m.group(1)
        # 提取 <a> 的文本
        text_m = re.search(r'<a[^>]*>([^<]+)</a>', inner)
        if not text_m:
            continue
        text = text_m.group(1).strip()
        if text == "不限":
            continue
        parsed = _parse_segment_text(text)
        if parsed is not None:
            segments.append((text, parsed[0], parsed[1]))
    return segments


def _parse_segment_text(text: str):
    """解析单个面积档位文本 → (min, max)。

    - "50㎡以下"  → (0, 50)
    - "80-90㎡"   → (80, 90)
    - "190㎡以上" → (190, inf)
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
