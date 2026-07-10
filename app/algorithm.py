# -*- coding: utf-8 -*-
"""询价算法，纯函数，无 IO。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


def mean(prices: List[float]) -> Optional[float]:
    """计算平均值，空列表返回 None。"""
    valid = [p for p in prices if p is not None and p > 0]
    if not valid:
        return None
    return sum(valid) / len(valid)


@dataclass
class Decision:
    final_price: Optional[float]
    branch: str


def decide(
    quote_avg: Optional[float],
    deal_avg: Optional[float],
    diff_threshold: float = 0.10,
    no_deal_discount: float = 0.9,
) -> Decision:
    """最终取值规则。

    - 若同时有在售均价和成交均价：
      - 当 |quote_avg - deal_avg| / deal_avg <= 10% 时，取较低值
      - 否则只取成交均价
    - 若没有成交均价：
      - 取在售均价的 9 折
    - 若没有在售均价但有成交均价：
      - 直接取成交均价
    """
    if quote_avg is None and deal_avg is None:
        return Decision(final_price=None, branch="FAILED")

    if deal_avg is None:
        return Decision(
            final_price=quote_avg * no_deal_discount,
            branch="QUOTE_DISCOUNT",
        )

    if quote_avg is None:
        return Decision(final_price=deal_avg, branch="DEAL_ONLY")

    diff = abs(quote_avg - deal_avg) / deal_avg
    if diff <= diff_threshold:
        return Decision(final_price=min(quote_avg, deal_avg), branch="TAKE_LOWER")
    return Decision(final_price=deal_avg, branch="DEAL_ONLY")
