# -*- coding: utf-8 -*-
"""链家核心挂牌与成交 HTML 解析测试。"""

from app.rpa.parsers.lj import (
    parse_deal_records,
    parse_listing_snapshots,
)


def test_parse_listing_and_deal_records_for_storage():
    """正常房源：小区名/户型/面积/总价/单价全部提取。"""
    html = """
    <ul class="sellListContent">
      <li class="clear">
        <div class="positionInfo"><a>绿景虹湾</a></div>
        <div class="houseInfo"><a>绿景虹湾</a> | 3室2厅 | 87.57平米 | 东</div>
        <div class="priceInfo">
          <div class="totalPrice"><span>720</span></div>
          <div class="unitPrice" data-price="86788"><span>86,788元/平</span></div>
        </div>
      </li>
    </ul>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    s = snapshots[0]
    assert s.community_name == "绿景虹湾"
    assert s.layout == "3室2厅"
    assert s.area == 87.57
    assert s.total_price == 720.0
    assert s.unit_price == 86788.0

    deal_html = """
    <ul class="listContent">
      <li>
        <div class="title"><a>绿景虹湾 3室1厅 75.14平米</a></div>
        <div class="dealDate">2026.05.06</div>
        <div class="totalPrice"><span class="number">558</span>万</div>
        <div class="unitPrice"><span class="number">74262</span>元/平</div>
      </li>
    </ul>
    """
    records = parse_deal_records(deal_html)

    assert len(records) == 1
    area, date_str, total, price = records[0]
    assert area == 75.14
    assert date_str == "2026-05-06"  # 日期点转横线
    assert total == 558.0
    assert price == 74262.0
