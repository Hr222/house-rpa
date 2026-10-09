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
    """执行估价算法所需的已整理价格输入。

    后四个字段（deal_samples / nearby_communities / area / luxury_data_sparse）
    中，area 与 luxury_data_sparse 为新老算法共用；deal_samples 与
    nearby_communities 仅供可比市场算法（DEFAULT）消费，老算法不读取，
    因此扩展字段对老算法零影响。
    """

    quote_price_lists: list[list[float]]
    weighted_median_discount: float = 0.9
    deal_price_lists: list[list[float]] = field(default_factory=list)
    # 目标小区可比成交单价：已跨平台去重、按请求面积 ±10% 与近半年筛选。
    # 空元组表示目标小区无可用成交样本（可比市场算法 case3/4 的入口条件）。
    deal_samples: tuple[float, ...] = ()
    # 周边小区数据（find_nearby_communities 取前 3）；仅在目标小区无成交时
    # 由编排层装配。老算法与"目标有成交"的链路均为空元组。
    nearby_communities: tuple["NearbyCommunityInput", ...] = ()
    area: Optional[float] = None
    luxury_data_sparse: bool = False


@dataclass(frozen=True)
class NearbyCommunityInput:
    """单个周边小区的可比数据（可比市场算法 case3/4 输入）。

    deal_prices：该小区成交单价，已跨平台去重并按与目标小区相同的
    口径（请求面积 ±10% + 近半年）筛选；空元组表示该小区无可用成交，
    case3 中不参与均值、case4 中不提供成交。
    quote_price_lists：该小区挂牌单价按请求面积 ±1㎡ 严格筛选后的
    分平台列表，仅供 case4 找各自的报价频率最高价。
    """

    community_id: int
    canonical_name: str
    deal_prices: tuple[float, ...] = ()
    quote_price_lists: list[list[float]] = field(default_factory=list)


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
