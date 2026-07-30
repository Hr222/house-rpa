# -*- coding: utf-8 -*-
"""行舟深房接口 parser 单测。"""

from datetime import date, timedelta

from app.parsers.xzsfbj import (
    filter_deal_records,
    find_community_candidates,
    match_community,
    parse_deal_page,
    parse_listing_snapshots,
    parse_community_index,
    is_residential_community,
)


def test_parse_deal_page_and_convert_prices():
    recent = (date.today() - timedelta(days=10)).isoformat()
    total, records = parse_deal_page({
        "count": 2,
        "dealList": [
            {"acreage": "92.0", "unitPrice": "3.30", "date": recent, "price": 304},
            {"acreage": 88, "unitPrice": 2.02, "date": recent, "price": 180},
        ],
    })

    assert total == 2
    prices, parsed = filter_deal_records(records, 91.5, 1.0)
    assert prices == [33000.0]
    assert parsed[0].area == 92.0
    assert parsed[0].date == recent
    assert parsed[0].total_price == 304.0


def test_filter_deal_records_keeps_only_matching_area_and_recent_date():
    recent = (date.today() - timedelta(days=10)).isoformat()
    old = (date.today() - timedelta(days=181)).isoformat()
    prices, parsed = filter_deal_records(
        [
            {"acreage": 91.0, "unitPrice": 3.1, "date": recent, "price": 280},
            {"acreage": 91.5, "unitPrice": 3.2, "date": old, "price": 290},
            {"acreage": 93.0, "unitPrice": 3.3, "date": recent, "price": 300},
            {"acreage": 91.5, "unitPrice": 3.4, "price": 310},
        ],
        area=91.5,
        tolerance=1.0,
    )

    assert prices == [31000.0]
    assert len(parsed) == 1
    assert parsed[0].date == recent


def test_filter_deal_records_accepts_slash_and_dot_date_formats():
    recent = date.today() - timedelta(days=10)
    prices, parsed = filter_deal_records(
        [
            {"acreage": 91.5, "unitPrice": 3.5, "date": recent.strftime("%Y/%m/%d")},
            {"acreage": 91.5, "unitPrice": 3.6, "date": recent.strftime("%Y.%m.%d")},
        ],
        area=91.5,
        tolerance=1.0,
    )

    assert prices == [35000.0, 36000.0]
    assert [record.date for record in parsed] == [recent.isoformat(), recent.isoformat()]


def test_parse_sales_to_listing_snapshots():
    snapshots = parse_listing_snapshots(
        [{"id": 1, "acreage": "93.71", "unitPrice": "4.8", "price": "450", "layout": "3室2厅"}],
        "月亮湾花园",
    )

    assert len(snapshots) == 1
    assert snapshots[0].community_name == "月亮湾花园"
    assert snapshots[0].unit_price == 48000.0
    assert snapshots[0].total_price == 450.0


def test_community_match_does_not_choose_first_ambiguous_candidate():
    communities = [
        {"name": "前海时代", "regionId": 1},
        {"name": "前海时代二期", "regionId": 2},
    ]

    assert len(find_community_candidates(communities, "前海")) == 2
    assert match_community(communities, "前海") is None


def test_community_alias_and_index_wrappers():
    communities = parse_community_index({"data": [{"name": "月亮湾花园", "rename": "月湾,月亮湾", "regionId": 2215}]})

    assert match_community(communities, "月湾")["regionId"] == 2215
    assert match_community(communities, "月亮湾")["regionId"] == 2215


def test_community_match_uses_administrative_district_for_disambiguation():
    communities = [
        {"name": "华润深圳湾瑞府", "rename": "泰瑞府", "area": "南山区", "regionId": 2567},
        {"name": "深业泰瑞府", "rename": "", "area": "龙岗区", "regionId": 6112},
    ]

    candidates = find_community_candidates(communities, "泰瑞府", "龙岗区")

    assert [item["regionId"] for item in candidates] == [6112]
    assert find_community_candidates(communities, "泰瑞府", "南山区")[0]["regionId"] == 2567


def test_community_match_keeps_same_district_ambiguity():
    communities = [
        {"name": "前海花园一期", "area": "南山区", "regionId": 1},
        {"name": "前海花园二期", "area": "南山区", "regionId": 2},
    ]

    assert len(find_community_candidates(communities, "前海花园", "南山区")) == 2
    assert match_community(communities, "前海花园", "南山区") is None


def test_phase_family_expands_when_only_one_phase_has_exact_alias():
    communities = [
        {
            "name": "中海怡翠山庄一期",
            "rename": "中海怡翠山庄",
            "area": "龙岗区",
            "regionId": 1,
        },
        {"name": "中海怡翠山庄二期", "area": "龙岗区", "regionId": 2},
        {"name": "中海怡翠山庄三期", "area": "龙岗区", "regionId": 3},
    ]

    candidates = find_community_candidates(
        communities, "中海怡翠山庄", "龙岗区"
    )

    assert [item["regionId"] for item in candidates] == [1, 2, 3]


def test_community_match_excludes_explicit_shop_entries():
    communities = [
        {"name": "万科公园里一期", "area": "龙岗区", "regionId": 4105},
        {"name": "万科公园里商铺", "area": "龙岗区", "regionId": 5283},
    ]

    assert is_residential_community(communities[0]) is True
    assert is_residential_community(communities[1]) is False
    assert [item["regionId"] for item in find_community_candidates(
        communities, "万科公园里", "龙岗区"
    )] == [4105]
