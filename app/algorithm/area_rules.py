# -*- coding: utf-8 -*-
"""估价规则共用的面积区间。"""

from __future__ import annotations


LISTING_AREA_TOLERANCE = 1.0
DEAL_AREA_TOLERANCE = 5.0
# 可比市场算法（market_comparison）的成交面积口径：请求面积的相对容差。
# 与上面两个绝对容差（㎡）平行存在：老算法继续用 ±5㎡，新算法用 ±10%，
# 由激活的算法模式决定走哪套，互不干扰。
DEAL_AREA_RELATIVE_TOLERANCE = 0.10


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


def relative_area_bounds(
    area: float,
    tolerance: float = DEAL_AREA_RELATIVE_TOLERANCE,
) -> tuple[float, float]:
    """返回相对容差的可比面积区间，默认请求面积 ±10%。

    可比市场算法的成交样本口径：容差随面积缩放（50㎡ 为 ±5㎡，
    120㎡ 为 ±12㎡），大户型放宽、小户型收紧，与绝对容差二选一使用。
    """
    delta = abs(area) * tolerance
    return area - delta, area + delta
