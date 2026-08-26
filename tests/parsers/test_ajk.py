# -*- coding: utf-8 -*-
"""安居客核心挂牌 HTML 解析测试。"""

from app.rpa.parsers.ajk import parse_listing_snapshots


def test_parse_listing_snapshot_extracts_storage_fields():
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
