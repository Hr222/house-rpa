# -*- coding: utf-8 -*-
"""安居客平台 HTML 解析器。

从结果页提取在售房源快照与挂牌均价。安居客无成交记录，
业务上把挂牌均价当作 deal_prices 的替代（见 adapter 注释）。
"""

import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

from app.rpa.core.models import ListingSnapshot


def _extract_first(pattern, text, cast=float):
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return cast(m.group(1).replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_listing_snapshots(html: str, base_url: Optional[str] = None) -> list:
    """提取主结果区房源快照。

    base_url: 页面 URL（直达采集传入），用于把房源详情链接 urljoin 成绝对
    listing_url；不传时保留原始 href（可能含 &amp;，已 unescape）。

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

        # 房源详情链接：chunk 内 /prop/view/{id} 链接
        listing_url = None
        view_m = re.search(r'<a[^>]*href="([^"]*prop/view/[^"]*)"', chunk, re.S)
        if view_m:
            href = view_m.group(1)
            listing_url = urljoin(base_url, unescape(href)) if base_url else unescape(href)

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
                listing_url=listing_url,
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
    保留平台无成交记录时的业务数据兼容字段。
    """
    m = re.search(
        r'community-info-detail-price-money[^>]*>\s*<em[^>]*>\s*([\d,]+)\s*</em>\s*元\s*/?\s*㎡',
        html,
    )
    return float(m.group(1).replace(",", "")) if m else None
