# -*- coding: utf-8 -*-
"""安居客 HTML 解析器单元测试。

DOM 样例取自 parsers/ajk.py 的函数 docstring，验证解析逻辑正确性。
"""

from app.parsers.ajk import parse_community_avg_price, parse_listing_snapshots


def test_parse_listing_snapshots_extracts_basic_fields():
    """正常房源：户型/面积/小区名/总价/单价全部提取。"""
    html = """
    <div class="property">
      <p class="property-content-info-attribute"><span>3</span>室<span>2</span>厅<span>2</span>卫</p>
      <div class="property-content-info-comm-name">绿景虹湾</div>
      <p class="property-content-info-text">88.35㎡</p>
      <div class="property-price">
        <span class="property-price-total-num">530</span>
        <span class="property-price-average">59988元</span>
      </div>
    </div>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    s = snapshots[0]
    assert s.community_name == "绿景虹湾"
    assert s.area == 88.35
    assert s.layout == "3室2厅"
    assert s.unit_price == 59988.0
    assert s.total_price == 530.0


def test_parse_listing_snapshots_stops_at_guess_title():
    """主结果区在 list-guess-title 处截断，推荐区房源不采。"""
    html = """
    <div class="property">
      <span class="property-price-average">60000元</span>
    </div>
    <h3 class="list-guess-title">猜你喜欢</h3>
    <div class="property">
      <span class="property-price-average">99999元</span>
    </div>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    assert snapshots[0].unit_price == 60000.0


def test_parse_listing_snapshots_skips_empty_record():
    """既无单价也无总价的条目跳过。"""
    html = """
    <div class="property">
      <div class="property-content-info-comm-name">空房源</div>
    </div>
    <div class="property">
      <span class="property-price-average">50000元</span>
    </div>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    assert snapshots[0].unit_price == 50000.0


def test_parse_community_avg_price_extracts_value():
    """社区卡片挂牌均价正常提取。"""
    html = """
    <div class="community-info-detail-price">
      <p class="community-info-detail-price-money"><em>84,307</em>元/㎡</p>
    </div>
    """
    assert parse_community_avg_price(html) == 84307.0


def test_parse_community_avg_price_returns_none_when_absent():
    """无社区卡片时返回 None。"""
    assert parse_community_avg_price("<div>无均价</div>") is None
