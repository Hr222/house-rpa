# -*- coding: utf-8 -*-
"""链家 HTML 解析器单元测试。

DOM 样例取自 parsers/lj.py 的函数 docstring。
"""

from datetime import datetime, timedelta

from app.parsers.lj import (
    filter_deal_records,
    parse_deal_records,
    parse_deal_total_pages,
    parse_listing_snapshots,
    parse_listing_total_pages,
)


def test_parse_listing_snapshots_extracts_fields():
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
    assert s.unit_price == 86788.0  # 优先取 data-price


def test_parse_listing_snapshots_stops_at_guess_title():
    """主结果区在"猜你喜欢"处截断。"""
    html = """
    <li class="clear"><div class="unitPrice" data-price="80000"><span>80,000</span></div></li>
    猜你喜欢
    <li class="clear"><div class="unitPrice" data-price="99999"><span>99,999</span></div></li>
    """
    snapshots = parse_listing_snapshots(html)
    assert len(snapshots) == 1


def test_parse_listing_total_pages():
    html = 'page-data="{&quot;totalPage&quot;:3,&quot;curPage&quot;:1}"'
    assert parse_listing_total_pages(html) == 3
    assert parse_listing_total_pages("无分页") == 1


def test_parse_deal_records_extracts_fields():
    """成交记录：面积/日期/总价/单价。"""
    html = """
    <ul class="listContent">
      <li>
        <div class="title"><a>绿景虹湾 3室1厅 75.14平米</a></div>
        <div class="dealDate">2026.05.06</div>
        <div class="totalPrice"><span class="number">558</span>万</div>
        <div class="unitPrice"><span class="number">74262</span>元/平</div>
      </li>
    </ul>
    """
    records = parse_deal_records(html)

    assert len(records) == 1
    area, date_str, total, price = records[0]
    assert area == 75.14
    assert date_str == "2026-05-06"  # 日期点转横线
    assert total == 558.0
    assert price == 74262.0


def test_parse_deal_total_pages():
    html = 'totalPage&quot;:5'
    assert parse_deal_total_pages(html) == 5
    assert parse_deal_total_pages("") == 1


def test_filter_deal_records_strict_area_and_recent():
    """严格面积区间 + 近半年：超面积/超期被过滤。"""
    recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    records = [
        (75.0, recent, 500.0, 66000.0),   # 面积内 + 近期 → 保留
        (120.0, recent, 800.0, 66000.0),  # 超面积 → 过滤
        (80.0, old, 500.0, 62000.0),      # 超期 → 过滤
    ]
    filtered = filter_deal_records(records, area_min=70, area_max=90, months=6)
    assert len(filtered) == 1
    assert filtered[0][3] == 66000.0
