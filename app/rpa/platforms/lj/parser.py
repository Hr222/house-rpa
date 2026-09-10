# -*- coding: utf-8 -*-
"""链家平台 HTML 解析器。

链家是贝壳子公司，DOM 与贝壳高度一致（同代码库），解析逻辑相似但独立实现。
成交筛选由编排层按统一算法口径处理，解析器只返回原始记录。
"""

import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

from app.rpa.core.models import ListingSnapshot


def parse_listing_snapshots(html: str, base_url: Optional[str] = None) -> list:
    """从结果页主结果列表提取在售房源快照。

    base_url: 页面 URL（直达采集传入），用于把房源详情链接 urljoin 成绝对
    listing_url；不传时保留原始 href。

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

        # 营销标题 + 房源详情链接（div.title a，/ershoufang/{houseId}.html）
        title = None
        listing_url = None
        tit_m = re.search(r'<div class="title">.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
        if tit_m:
            href = tit_m.group(1)
            listing_url = urljoin(base_url, unescape(href)) if base_url else unescape(href)
            title = re.sub(r'<[^>]+>', '', tit_m.group(2)).strip() or None

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
                title=title,
                listing_url=listing_url,
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

    返回 [(面积, 日期, 总价万, 单价, 小区名), ...]，面积与小区名均从 title
    提取（小区名为标题前缀，供采集层归属过滤；无法识别时为 None）。
    日期格式统一转为 YYYY-MM-DD（原始是 2026.05.06）。
    """
    records = []
    for m in re.finditer(r'<li[^>]*>(.*?)(?=<li[^>]*>|</ul>)', html, re.S):
        chunk = m.group(1)
        if "dealDate" not in chunk:
            continue

        community_name = None
        area = None
        title_m = re.search(r'<div class="title">.*?>(.*?)</a>', chunk, re.S)
        if title_m:
            title_text = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
            # 小区名 = 户型“N室”之前的标题前缀；“3期”“168”等名称内数字不跟“室”，不受影响
            name_m = re.match(r'^(.*?)\s*\d+\s*室', title_text)
            if name_m and name_m.group(1):
                community_name = name_m.group(1)
            area_m = re.search(r'([\d.]+)\s*平米', title_text)
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

        records.append((area, date_str, total_price, unit_price, community_name))
    return records


def parse_deal_total_pages(html: str) -> int:
    """从成交页分页区解析总页数。"""
    m = re.search(r'totalPage&quot;:(\d+)', html or "")
    return int(m.group(1)) if m else 1
