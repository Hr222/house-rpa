# -*- coding: utf-8 -*-
"""贝壳 adapter 的成交面积回归测试。"""

from app.core.models import DealRecord
from app.platforms.adapters.ke import _filter_deal_prices_for_request_area


def test_deal_prices_use_request_area_not_listing_segment():
    """90~110㎡在售档位不得把92.49㎡成交当成104.03㎡的可比成交。"""
    records = [
        DealRecord(area=89.1, unit_price=32324.0),
        DealRecord(area=89.1, unit_price=39933.0),
        DealRecord(area=92.49, unit_price=40654.0),
        DealRecord(area=99.03, unit_price=35000.0),
    ]

    assert _filter_deal_prices_for_request_area(records, 104.03) == [35000.0]
