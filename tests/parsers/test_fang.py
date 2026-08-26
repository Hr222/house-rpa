# -*- coding: utf-8 -*-
"""房天下核心挂牌与成交 HTML 解析测试。"""

from app.rpa.parsers.fang import (
    parse_deal_records,
    parse_listing_snapshots,
)


def test_parse_listing_and_deal_records_for_storage():
    """正常房源：小区名(add_shop)/标题(tit_shop)/户型/面积/总价/单价。"""
    html = """
    <dl class="clearfix">
      <dd><h4><a><span class="tit_shop">绿景虹湾 精装三房</span></a></h4>
          <p class="tel_shop">3室2厅 | 88.35㎡ | 南北</p>
          <p class="add_shop"><a href="//sz.fang.com/house-xm2810892392/" title="绿景虹湾"> 绿景虹湾 </a><span>梅林 北环大道6098号</span></p></dd>
      <dd class="price_right"><span class="red"><b>530</b>万</span><span>59988元/㎡</span></dd>
    </dl>
    """
    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    s = snapshots[0]
    assert s.community_name == "绿景虹湾"  # add_shop a 提取
    assert s.title == "绿景虹湾 精装三房"     # tit_shop 提取
    assert s.layout == "3室2厅"
    assert s.area == 88.35
    assert s.total_price == 530.0
    assert s.unit_price == 59988.0

    deal_html = """
    <table class="table_hx"><tbody>
      <tr><th>房源面积</th><th>成交时间</th><th>成交总价</th><th>成交均价</th></tr>
      <tr><td><p>75.14㎡</p></td><td><p>2026-05-06</p></td><td><p>558万</p></td><td><p>74262元/㎡</p></td></tr>
    </tbody></table>
    """
    records = parse_deal_records(deal_html)

    assert len(records) == 1
    area, date_str, total, price = records[0]
    assert area == 75.14
    assert date_str == "2026-05-06"
    assert total == 558
    assert price == 74262.0
