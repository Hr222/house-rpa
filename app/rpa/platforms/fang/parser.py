# -*- coding: utf-8 -*-
"""房天下平台 HTML 解析器。

从结果页提取在售房源快照、从成交页表格提取成交记录。
成交筛选由编排层按统一算法口径处理，解析器只返回原始记录。
"""

import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

from app.rpa.core.models import ListingSnapshot


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


def parse_listing_snapshots(html: str, base_url: Optional[str] = None) -> list:
    """从主结果区提取在售房源快照。

    base_url: 页面 URL（直达采集传入），用于把房源详情链接 urljoin 成绝对
    listing_url；不传时保留原始 href。

    DOM:
      <dl class="clearfix ...">
        <dd><h4><a><span class="tit_shop">小区名 房源标题...</span></a></h4>
            <p class="tel_shop">3室2厅 | 88.35㎡ | ...</p></dd>
        <dd class="price_right"><span class="red"><b>530</b>万</span><span>59988元/㎡</span></dd>
      </dl>

    边界：同时截断两类推荐位——二手房推荐"您可能感兴趣的房源"
    （tit_x 标题，房源少的单条/空态页会补其他小区推荐，见 dump
    20260903_1805_fang_listing_单条_莲塘派出所宿舍）与新房推荐
    "InterestedNewHouse"；取最早出现位置。
    注意：tit_shop 是营销标题（非小区名），真正的小区名在 p.add_shop a 里。
    """
    cut_a = html.find("您可能感兴趣的房源")
    cut_b = html.find("InterestedNewHouse")
    candidates = [c for c in (cut_a, cut_b) if c > 0]
    cut = min(candidates) if candidates else -1
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(r'<dl class="clearfix[^"]*"[^>]*>(.*?)</dl>', main_html, re.S):
        chunk = block.group(1)

        # 营销标题：tit_shop（h4 内 span）
        title = None
        title_m = re.search(r'tit_shop[^>]*>(.*?)</span>', chunk, re.S)
        if title_m:
            title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() or None

        # 房源详情链接：h4 内 <a href>（/chushou/{id}.htm）
        listing_url = None
        link_m = re.search(r'<h4[^>]*>\s*<a[^>]*href="([^"]+)"', chunk, re.S)
        if link_m:
            href = link_m.group(1)
            listing_url = urljoin(base_url, unescape(href)) if base_url else unescape(href)

        # 小区名：add_shop 里的 <a> 链接（title 属性或文本）
        community_name = None
        add_m = re.search(r'add_shop[^>]*>.*?<a[^>]*>(.*?)</a>', chunk, re.S)
        if add_m:
            community_name = re.sub(r'<[^>]+>', '', add_m.group(1)).strip() or None

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
                title=title,
                listing_url=listing_url,
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
