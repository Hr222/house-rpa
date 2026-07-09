# -*- coding: utf-8 -*-
"""询价算法（纯函数，无 IO）。需求 §3.6。"""
from typing import List, Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class Decision:
    """取舍决策结果。"""
    final_price: Optional[float]
    branch: str  # TAKE_LOWER / DEAL_ONLY / QUOTE_DISCOUNT / FAILED


def filter_by_area(items: List[Dict[str, Any]],
                   area_min: float, area_max: float) -> List[Dict[str, Any]]:
    """按面积区间过滤。面积缺失(None)的剔除。闭区间。
    区间由后端给定，RPA 不算±20%。
    """
    return [it for it in items
            if it.get("area") is not None and area_min <= it["area"] <= area_max]


def mean_price(items: List[Dict[str, Any]]) -> Optional[float]:
    """对 unit_price 字段求算术平均。无有效值返回 None。"""
    prices = [it["unit_price"] for it in items
              if it.get("unit_price") is not None]
    if not prices:
        return None
    return sum(prices) / len(prices)


def decide_final_price(quote_avg: Optional[float],
                       deal_avg: Optional[float],
                       diff_threshold: float = 0.10,
                       no_deal_discount: float = 0.8) -> Decision:
    """§3.6 取舍算法。

    - 无报价无成交 → FAILED
    - 无成交 → 报价 × 折扣 (QUOTE_DISCOUNT)
    - 无报价有成交 → 成交价 (DEAL_ONLY)
    - 差异 ≤ 阈值 → 取低 (TAKE_LOWER)
    - 差异 > 阈值 → 只取成交 (DEAL_ONLY)
    """
    if quote_avg is None and deal_avg is None:
        return Decision(final_price=None, branch="FAILED")

    if deal_avg is None:
        return Decision(final_price=quote_avg * no_deal_discount,
                        branch="QUOTE_DISCOUNT")

    if quote_avg is None:
        return Decision(final_price=deal_avg, branch="DEAL_ONLY")

    diff = abs(quote_avg - deal_avg) / deal_avg
    if diff <= diff_threshold:
        return Decision(final_price=min(quote_avg, deal_avg),
                        branch="TAKE_LOWER")
    return Decision(final_price=deal_avg, branch="DEAL_ONLY")
