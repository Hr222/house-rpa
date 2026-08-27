# -*- coding: utf-8 -*-
"""房产询价算法的输入、输出和中间模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Decision:
    """一次算法决策的最终价格和分支。"""

    final_price: Optional[float]
    branch: str


@dataclass
class AlgorithmInput:
    """执行估价算法所需的已整理价格输入。"""

    quote_price_lists: list[list[float]]
    weighted_median_discount: float = 0.9
    deal_price_lists: list[list[float]] = field(default_factory=list)
    area: Optional[float] = None
    luxury_data_sparse: bool = False


@dataclass(frozen=True)
class PriceCandidate:
    """按挂牌频次选出的显著价格峰值。"""

    quote_price: float
    final_price: float
    count: int
    frequency: float
    min_price: float
    max_price: float


@dataclass
class AlgorithmEvaluation:
    """算法计算出的价格均值、最终决策和候选峰。"""

    quote_avg: Optional[float]
    deal_avg: Optional[float]
    decision: Decision
    candidates: list[PriceCandidate] = field(default_factory=list)


class AlgorithmStrategy(Protocol):
    """估价算法实现的稳定扩展接口。"""

    def evaluate(self, inputs: AlgorithmInput) -> AlgorithmEvaluation:
        ...


@dataclass(frozen=True)
class WeightedMedianDiagnostic:
    """解释加权中位数候选价格簇的诊断信息。"""

    prices: tuple[float, ...]
    center: float
    coverage: float
    max_relative_deviation: float
