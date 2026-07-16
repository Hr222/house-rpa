# -*- coding: utf-8 -*-

from app.core.models import DealRecord
from app.parsers.ke import (
    filter_deal_prices_by_area,
    find_detail_link,
    find_sold_list_url,
    parse_deal_prices,
    parse_deal_records,
    parse_listing_prices,
    parse_listing_records,
    parse_listing_snapshots,
)


def test_parse_listing_prices_skips_goodhouse_ads():
    html = """
    <ul class="sellListContent" log-mod="list">
      <li class="clear">
        <div class="unitPrice" data-hid="1"><span>84,547元/平</span></div>
      </li>
      <li class="list_goodhouse_daoliu clear">
        <div class="unitPrice" data-hid="2"><span>99,999元/平</span></div>
      </li>
      <li class="clear">
        <div class="unitPrice" data-hid="3"><span>81,123元/平</span></div>
      </li>
    </ul>
    """

    assert parse_listing_prices(html) == [84547.0, 81123.0]
    assert parse_listing_records(html) == [("1", 84547.0), ("3", 81123.0)]


def test_parse_listing_prices_ignores_guess_you_like_block():
    html = """
    <ul class="sellListContent" log-mod="list">
      <li class="clear">
        <div class="unitPrice" data-hid="1001"><span>84,547元/平</span></div>
      </li>
      <li class="clear">
        <div class="unitPrice" data-hid="1002"><span>87,628元/平</span></div>
      </li>
    </ul>
    <div class="recommendList" id="lessResultWrap">
      <h2>猜你喜欢</h2>
      <ul class="sellListContent VIEWDATA" data-component="lessResult">
        <li class="clear">
          <div class="unitPrice" data-hid="2001"><span>70,047元/平</span></div>
        </li>
        <li class="clear">
          <div class="unitPrice" data-hid="2002"><span>71,825元/平</span></div>
        </li>
      </ul>
    </div>
    """

    assert parse_listing_prices(html) == [84547.0, 87628.0]
    assert parse_listing_records(html) == [("1001", 84547.0), ("1002", 87628.0)]


def test_parse_listing_snapshots_extracts_summary_fields():
    html = """
    <ul class="sellListContent" log-mod="list">
      <li class="clear">
        <div class="positionInfo"><a>绿景虹湾</a></div>
        <div class="houseInfo">中楼层 | 2015年 | 3室2厅 | 85.16平米 | 西南</div>
        <div class="priceInfo">
          <div class="totalPrice totalPrice2"><span>720</span><i>万</i></div>
          <div class="unitPrice" data-hid="1001"><span>84,547元/平</span></div>
        </div>
      </li>
    </ul>
    """

    snapshots = parse_listing_snapshots(html)

    assert len(snapshots) == 1
    assert snapshots[0].house_id == "1001"
    assert snapshots[0].community_name == "绿景虹湾"
    assert snapshots[0].layout == "3室2厅"
    assert snapshots[0].area == 85.16
    assert snapshots[0].unit_price == 84547.0
    assert snapshots[0].total_price == 720.0


def test_parse_deal_records_extracts_area_and_unit_price():
    html = """
    <div class="dealList">
      <li>2室1厅 89.31平米 南 2025.03 成交 76,500元/平</li>
      <li>3室1厅 101.50平米 南北 2025.04 成交 72,100元/平</li>
    </div>
    """

    records = parse_deal_records(html)

    assert records == [
        DealRecord(area=89.31, unit_price=76500.0),
        DealRecord(area=101.50, unit_price=72100.0),
    ]
    assert parse_deal_prices(html) == [76500.0, 72100.0]


def test_parse_deal_records_reads_embedded_sold_array():
    html = r"""
    <script>
    window.__DATA__ = {
      "sold":[
        {"area":75.14,"unitPrice":74262,"viewUrl":"https:\/\/sz.ke.com\/chengjiao\/105124125237.html"},
        {"area":87.57,"unitPrice":"96837","viewUrl":"https:\/\/sz.ke.com\/chengjiao\/105119840425.html"}
      ],
      "soldUrl":"https:\/\/sz.ke.com\/chengjiao\/c2411063588287\/"
    };
    </script>
    """

    assert parse_deal_records(html) == [
        DealRecord(area=75.14, unit_price=74262.0),
        DealRecord(area=87.57, unit_price=96837.0),
    ]
    assert find_sold_list_url(html) == "https://sz.ke.com/chengjiao/c2411063588287/"


def test_find_detail_link_reads_agent_card_link():
    html = """
    <a class="agentCardResblockLink LOGCLICK" href="https://sz.ke.com/xiaoqu/2411063588287/">
      查看小区详情
    </a>
    """

    assert find_detail_link(html) == "https://sz.ke.com/xiaoqu/2411063588287/"


def test_filter_deal_prices_by_area_strict_range():
    """严格面积区间（与链家/房天下统一口径，不再用 ±20% 容差）。"""
    records = [
        DealRecord(area=55.0, unit_price=50000.0),
        DealRecord(area=70.0, unit_price=60000.0),
        DealRecord(area=90.0, unit_price=70000.0),
        DealRecord(area=108.0, unit_price=80000.0),
        DealRecord(area=120.0, unit_price=90000.0),
    ]

    # 严格区间 70~90：只保留 70 和 90（55/108/120 排除）
    assert filter_deal_prices_by_area(records, 70.0, 90.0) == [
        60000.0,
        70000.0,
    ]
