# -*- coding: utf-8 -*-
"""估价规则共用的面积区间。"""

from __future__ import annotations


LISTING_AREA_TOLERANCE = 1.0
DEAL_AREA_TOLERANCE = 5.0


def listing_area_bounds(
    area: float,
    tolerance: float = LISTING_AREA_TOLERANCE,
) -> tuple[float, float]:
    """返回可比挂牌面积区间，默认请求面积 ±1㎡。"""
    return area - tolerance, area + tolerance


def deal_area_bounds(
    area: float,
    tolerance: float = DEAL_AREA_TOLERANCE,
) -> tuple[float, float]:
    """返回可比成交面积区间，默认请求面积 ±5㎡。"""
    return listing_area_bounds(area, tolerance)
