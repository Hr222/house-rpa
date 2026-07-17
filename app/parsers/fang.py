# -*- coding: utf-8 -*-
"""房天下平台 HTML 解析器。

从结果页提取在售房源快照、从成交页表格提取成交记录。
成交筛选用严格面积区间 + 近半年（贝壳用 ±20% 容差，两者口径不同，不混用）。
"""

import re
from datetime import datetime, timedelta
from typing import Optional

from app.core.models import ListingSnapshot


def parse_total_pages(html: str) -> int:
    """从房天下分页区解析总页数。

    DOM: <span class="last">共2页</span>
    """
    m = re.search(r'共(\d+)页', html or "")
    return int(m.group(1)) if m else 1


def parse_current_page(html: str) -> Optional[int]:
    """从房天下分页区解析当前页码。

    DOM: <span class="on">1</span>（当前页带 class=on）
    """
    m = re.search(r'<span class="on">(\d+)</span>', html or "")
    return int(m.group(1)) if m else None


def find_deal_link(html: str) -> Optional[str]:
    """从详情页 HTML 中提取成交页链接。

    DOM:
      <a href="//xxx.fang.com/loupan/123456/chengjiao/">成交记录</a>

    Returns:
        完整成交页 URL（补全 https: 前缀），未找到返回 None。
    """
    m = re.search(
        r'href=["\'](//[^"\']+?/loupan/\d+/chengjiao/[^"\']*)["\']',
        html or "",
    )
    if not m:
        return None
    raw = m.group(1)
    return f"https:{raw}" if raw.startswith("//") else raw


def parse_listing_snapshots(html: str) -> list:
    """从主结果区提取在售房源快照。

    DOM:
      <dl class="clearfix ...">
        <dd><h4><a><span class="tit_shop">小区名 房源标题...</span></a></h4>
            <p class="tel_shop">3室2厅 | 88.35㎡ | ...</p></dd>
        <dd class="price_right"><span class="red"><b>530</b>万</span><span>59988元/㎡</span></dd>
      </dl>

    边界：截断到"您可能感兴趣的新房"(InterestedNewHouse)之前，排除新房推荐位。
    注意：房天下的 tit_shop 是房源标题（含小区名），不像安居客有独立小区名字段。
    """
    cut = html.find("InterestedNewHouse")
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(r'<dl class="clearfix[^"]*"[^>]*>(.*?)</dl>', main_html, re.S):
        chunk = block.group(1)

        # 小区名：tit_shop 取第一个词（房天下 tit_shop 是房源标题，含小区名）
        name_m = re.search(r'tit_shop[^>]*>(.*?)</span>', chunk, re.S)
        community_name = None
        if name_m:
            clean = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()
            community_name = clean.split()[0] if clean else None

        # 户型+面积：tel_shop 里 "3室2厅 | 88.35㎡ | ..."
        tel_m = re.search(r'tel_shop[^>]*>(.*?)</p>', chunk, re.S)
        layout = None
        area = None
        if tel_m:
            tel_text = re.sub(r'<[^>]+>', '', tel_m.group(1))
            layout_m = re.search(r'(\d+室\d+厅)', tel_text)
            if layout_m:
                layout = layout_m.group(1)
            area_m = re.search(r'([\d.]+)\s*㎡', tel_text)
            if area_m:
                area = float(area_m.group(1))

        # 总价(万)
        total_m = re.search(r'<b>([\d,]+)</b>\s*万', chunk)
        total_price = float(total_m.group(1).replace(",", "")) if total_m else None

        # 单价
        price_m = re.search(r'<span>([\d,]+)\s*元/㎡</span>', chunk)
        unit_price = float(price_m.group(1).replace(",", "")) if price_m else None

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


def parse_deal_records(html: str) -> list:
    """从成交页表格解析成交记录。

    DOM:
      <table class="table_hx"><tbody>
        <tr><th>房源面积</th><th>成交时间</th><th>成交总价</th><th>成交均价</th><th>信息来源</th></tr>
        <tr><td><p>75.14㎡</p></td><td><p>2026-05-06</p></td><td><p>558万</p></td><td><p>74262元/㎡</p></td>...</tr>

    返回 [(面积, 日期, 总价万, 单价), ...] 原始列表，不过滤。
    """
    rows = re.findall(
        r'<td><p>([\d.]+)\s*㎡</p></td>\s*'
        r'<td><p>(\d{4}-\d{2}-\d{2})</p></td>\s*'
        r'<td><p>([\d]+)万</p></td>\s*'
        r'<td><p>([\d,]+)\s*元/㎡</p></td>',
        html,
    )
    records = []
    for area_str, date_str, total_str, price_str in rows:
        try:
            records.append((
                float(area_str),
                date_str,
                int(total_str),
                float(price_str.replace(",", "")),
            ))
        except ValueError:
            continue
    return records


def filter_deal_records(records: list, area_min: float, area_max: float, months: int = 6) -> list:
    """过滤成交记录：严格面积区间 + 近 months 个月。

    房天下规则：严格面积区间 area_min~area_max，日期 >= 今天往前 months 个月。
    贝壳/链家/房天下已统一为严格面积区间口径。
    """
    cutoff = datetime.now() - timedelta(days=30 * months)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    filtered = []
    for area, date_str, total, price in records:
        if not (area_min <= area <= area_max):
            continue
        if date_str < cutoff_str:
            continue
        filtered.append((area, date_str, total, price))
    return filtered


def parse_area_segments(html: str) -> list:
    """从结果页面积筛选区动态读取面积档位。

    房天下面积筛选区 DOM：
      <li class="clearfix screen_list">
        <span class="screen_title">面积</span>
        <ul class="clearfix choose_screen">
          <li><label><span class="icon_check"></span><a href=".../j20-k250/">50平米以下</a></label></li>
          <li><label><span class="icon_check"></span><a href=".../j270-k290/">70-90平米</a></label></li>
          <li><label><span class="icon_check"></span><a href=".../j2300-k20/">300平米以上</a></label></li>
          <li class="text_inp" name="customarea">...</li>  ← 自定义输入框，跳过

    档位因城市而异（深圳 9 档、汕尾 8 档），必须从 HTML 动态读取。
    注意：房天下用"平米"不是"㎡"。

    Returns:
        [(text, min, max), ...]，按页面顺序。
        - "50平米以下"  → (0, 50)
        - "70-90平米"   → (70, 90)
        - "300平米以上" → (300, inf)
        自定义输入框跳过。
    """
    # 定位面积筛选区：screen_title">面积 到下一个 screen_list
    title_match = re.search(r'screen_title[^>]*>面积', html)
    if not title_match:
        return []
    start = title_match.end()
    # 找下一个 screen_list（特色/朝向等），截断面积区
    next_section = re.search(r'screen_title', html[start:])
    end = start + next_section.start() if next_section else start + 5000
    chunk = html[start:end]

    segments = []
    # 匹配 li（排除 text_inp 自定义输入框）
    for m in re.finditer(
        r'<li[^>]*(?!class="text_inp")[^>]*>(.*?)</li>',
        chunk, re.S,
    ):
        inner = m.group(1)
        if "customarea" in inner or "cminArea" in inner:
            continue
        # 提取 <a> 的文本
        text_m = re.search(r'<a[^>]*>([^<]*平米[^<]*)</a>', inner)
        if not text_m:
            continue
        text = text_m.group(1).strip()
        parsed = _parse_segment_text(text)
        if parsed is not None:
            segments.append((text, parsed[0], parsed[1]))
    return segments


def _parse_segment_text(text: str):
    """解析单个面积档位文本 → (min, max)。

    房天下用"平米"单位：
    - "50平米以下"  → (0, 50)
    - "70-90平米"   → (70, 90)
    - "300平米以上" → (300, inf)
    """
    text = text.strip()

    # XX平米以下
    m = re.match(r'(\d+)\s*平米?\s*以下', text)
    if m:
        return (0.0, float(m.group(1)))

    # XX-YY平米
    m = re.match(r'(\d+)\s*[-~]\s*(\d+)\s*平米?', text)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    # XX平米以上
    m = re.match(r'(\d+)\s*平米?\s*以上', text)
    if m:
        return (float(m.group(1)), float('inf'))

    return None
