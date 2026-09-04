# -*- coding: utf-8 -*-
"""乐有家核心挂牌 HTML 解析测试。"""

from app.rpa.platforms.lyj.parser import parse_listing_snapshots


def test_parse_listing_snapshot_extracts_storage_fields():
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
