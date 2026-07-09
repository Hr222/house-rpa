# -*- coding: utf-8 -*-
"""询价算法（纯函数，无 IO）。需求 §3.6。

两个均值：
  P_quote = 报价均值（详情页小区均价，或各平台在售单价平均）
  P_deal  = 成交均值（成交单价按面积区间筛选后平均）

取舍规则：
  有 P_deal:
    diff = |P_quote - P_deal| / P_deal
    diff ≤ 10% → min(P_quote, P_deal)   取低
    diff > 10% → P_deal                  只取成交价
  无 P_deal:
    P_quote × 0.8                         报价 8 折
"""
from typing import List, Optional
from dataclasses import dataclass


def mean(prices: List[float]) -> Optional[float]:
    """算术平均。空列表返回 None。"""
    valid = [p for p in prices if p is not None and p > 0]
    if not valid:
        return None
    return sum(valid) / len(valid)


@dataclass
class Decision:
    final_price: Optional[float]
    branch: str


def decide(quote_avg: Optional[float], deal_avg: Optional[float],
           diff_threshold: float = 0.10, no_deal_discount: float = 0.8) -> Decision:
    """§3.6 取舍算法。"""
    if quote_avg is None and deal_avg is None:
        return Decision(final_price=None, branch="FAILED")

    if deal_avg is None:
        return Decision(final_price=quote_avg * no_deal_discount,
                        branch="QUOTE_DISCOUNT")

    if quote_avg is None:
        return Decision(final_price=deal_avg, branch="DEAL_ONLY")

    diff = abs(quote_avg - deal_avg) / deal_avg
    if diff <= diff_threshold:
        return Decision(final_price=min(quote_avg, deal_avg), branch="TAKE_LOWER")
    return Decision(final_price=deal_avg, branch="DEAL_ONLY")
