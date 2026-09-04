"""贝壳核心挂牌 HTML 解析测试。"""

from app.rpa.platforms.ke.parser import (
    find_detail_link,
    parse_listing_snapshots,
)


def test_parse_listing_snapshot_and_community_page():
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
    page_html = """
    <a class="agentCardResblockLink LOGCLICK" href="https://sz.ke.com/xiaoqu/2411063588287/">
      查看小区详情
    </a>
    """
    assert find_detail_link(page_html) == "https://sz.ke.com/xiaoqu/2411063588287/"
