# -*- coding: utf-8 -*-
"""房产询价算法模块。"""

from app.algorithm.config import (
    get_weighted_median_discount,
    get_weak_area_max_tolerance,
    is_weighted_median_discount_default,
    set_weighted_median_discount,
)
from app.algorithm.weighted_median import evaluate_algorithm

__all__ = [
    "evaluate_algorithm",
    "get_weighted_median_discount",
    "get_weak_area_max_tolerance",
    "is_weighted_median_discount_default",
    "set_weighted_median_discount",
]
