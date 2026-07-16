# -*- coding: utf-8 -*-
"""房天下 HTML 解析器单元测试。

DOM 样例取自 parsers/fang.py 的函数 docstring。
"""

from datetime import datetime, timedelta

from app.parsers.fang import (
    filter_deal_records,
    parse_current_page,
    parse_deal_records,
    parse_listing_snapshots,
    parse_total_pages,
)


def test_parse_total_pages():
    assert parse_total_pages('<span class="last">共2页</span>') == 2
    assert parse_total_pages("无分页") == 1


def test_parse_current_page():
    assert parse_current_page('<span class="on">3</span>') == 3
    assert parse_current_page("无页码") is None


def test_parse_listing_snapshots_extracts_fields():
    """正常房源：小区名/户型/面积/总价/单价。"""
    html = """
    <dl class="clearfix">
      <dd><h4><a><span class="tit_shop">绿景虹湾 精装三房</span></a></h4>
          <p class="tel_shop">3室2厅 | 88.35㎡ | 南北</p></dd>
      <dd class="price_right"><span class="red"><b>530</b>万</span><span>59988元/㎡</span></dd>
    </dl>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    s = snapshots[0]
    assert s.community_name == "绿景虹湾"  # tit_shop 取第一个词
    assert s.layout == "3室2厅"
    assert s.area == 88.35
    assert s.total_price == 530.0
    assert s.unit_price == 59988.0


def test_parse_listing_snapshots_stops_at_interested_new_house():
    """主结果区在 InterestedNewHouse 处截断。"""
    html = """
    <dl class="clearfix"><dd class="price_right"><span>60000元/㎡</span></dd></dl>
    <div class="InterestedNewHouse">您可能感兴趣的新房</div>
    <dl class="clearfix"><dd class="price_right"><span>99999元/㎡</span></dd></dl>
    """
    snapshots = parse_listing_snapshots(html)
    assert len(snapshots) == 1
    assert snapshots[0].unit_price == 60000.0


def test_parse_deal_records_extracts_table_rows():
    """成交表格行解析：面积/日期/总价/单价。"""
    html = """
    <table class="table_hx"><tbody>
      <tr><th>房源面积</th><th>成交时间</th><th>成交总价</th><th>成交均价</th></tr>
      <tr><td><p>75.14㎡</p></td><td><p>2026-05-06</p></td><td><p>558万</p></td><td><p>74262元/㎡</p></td></tr>
    </tbody></table>
    """
    records = parse_deal_records(html)

    assert len(records) == 1
    area, date_str, total, price = records[0]
    assert area == 75.14
    assert date_str == "2026-05-06"
    assert total == 558
    assert price == 74262.0


def test_filter_deal_records_strict_area_and_recent():
    """严格面积区间 + 近半年。"""
    recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    records = [
        (75.0, recent, 558, 74000.0),   # 面积内 + 近期 → 保留
        (120.0, recent, 800, 66000.0),  # 超面积 → 过滤
        (80.0, old, 500, 62000.0),      # 超期 → 过滤
    ]
    filtered = filter_deal_records(records, area_min=70, area_max=90, months=6)
    assert len(filtered) == 1
    assert filtered[0][3] == 74000.0
