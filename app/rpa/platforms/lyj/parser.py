# -*- coding: utf-8 -*-
"""乐有家平台 HTML 解析器。

从结果页提取在售房源快照与小区均价。乐有家无成交记录，
业务上用小区均价当作 deal_prices 的替代（见 adapter 注释）。
"""

import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

from app.rpa.core.models import ListingSnapshot


def _normalize_text(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_listing_snapshots(html: str, base_url: Optional[str] = None) -> list:
    """从乐有家搜索结果页提取房源快照。

    base_url: 页面 URL（直达采集传入），用于把房源详情链接 urljoin 成绝对
    listing_url；不传时保留原始 href。

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
        comm_m = re.search(r'href="[^"]*xq/detail/\d+[^"]*"[^>]*>(?:<[^>]+>)*\s*([^<]+)', chunk)
        if comm_m:
            community_name = _normalize_text(comm_m.group(1))

        # 营销标题 + 房源详情链接（p.tit a，href /esf/detail/{id}）
        title = None
        listing_url = None
        tit_m = re.search(r'<p class="tit">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
        if tit_m:
            href = tit_m.group(1)
            listing_url = urljoin(base_url, unescape(href)) if base_url else unescape(href)
            title = _normalize_text(tit_m.group(2)) or None

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
