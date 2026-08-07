# -*- coding: utf-8 -*-
"""各平台成交面积口径回归测试。"""

from datetime import datetime, timedelta

from app.platforms.adapters.fang import _filter_deals_for_request_area as filter_fang
from app.platforms.adapters.lj import _filter_deals_for_request_area as filter_lj


def test_fang_and_lj_deals_use_request_area_plus_minus_five():
    """成交不再跟随90~110㎡在售档位，且保留请求面积 ±5㎡记录。"""
    recent = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    records = [
        (92.49, recent, 376.0, 40654.0),
        (99.03, recent, 350.0, 35000.0),
        (109.03, recent, 365.0, 36000.0),
    ]

    assert [record[3] for record in filter_fang(records, 104.03)] == [35000.0, 36000.0]
    assert [record[3] for record in filter_lj(records, 104.03)] == [35000.0, 36000.0]
