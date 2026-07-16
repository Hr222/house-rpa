# -*- coding: utf-8 -*-
"""乐有家 HTML 解析器单元测试。

DOM 样例取自 parsers/lyj.py 的函数 docstring。
"""

from app.parsers.lyj import (
    parse_community_avg_price,
    parse_current_page,
    parse_listing_snapshots,
    parse_total_pages,
)


def test_parse_listing_snapshots_extracts_fields():
    """正常房源：小区名/户型/面积/总价/单价。"""
    html = """
    <li class="item clearfix">
      <p class="tit"><a>精装三房</a></p>
      <p class="attr">
        <span>3室2厅1卫 / 建筑面积73.5㎡</span>
        <a href="/xq/detail/123456/">绿景虹湾</a>
      </p>
      <span class="salePrice">320</span>万
      <p class="sub">单价44218元/㎡</p>
    </li>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    s = snapshots[0]
    assert s.community_name == "绿景虹湾"
    assert s.layout == "3室2厅"
    assert s.area == 73.5
    assert s.total_price == 320.0
    assert s.unit_price == 44218.0


def test_parse_listing_snapshots_stops_at_guess_title():
    """主结果区在"猜你喜欢"处截断。"""
    html = """
    <li class="item clearfix"><p class="sub">单价50000元/㎡</p></li>
    猜你喜欢
    <li class="item clearfix"><p class="sub">单价99999元/㎡</p></li>
    """
    snapshots = parse_listing_snapshots(html)
    assert len(snapshots) == 1
    assert snapshots[0].unit_price == 50000.0


def test_parse_community_avg_price_extracts_value():
    """社区信息卡小区均价。"""
    html = '<em class="label">小区均价</em><em class="txt">54,386元/㎡</em>'
    assert parse_community_avg_price(html) == 54386.0


def test_parse_community_avg_price_returns_none_when_absent():
    assert parse_community_avg_price("<div>无均价</div>") is None


def test_parse_total_pages():
    html = '<a title="5" href="...">尾页</a>'
    assert parse_total_pages(html) == 5
    assert parse_total_pages("无分页") == 1


def test_parse_current_page():
    html = '<a class="on" href="...">3</a>'
    assert parse_current_page(html) == 3
    assert parse_current_page("无页码") == 1
