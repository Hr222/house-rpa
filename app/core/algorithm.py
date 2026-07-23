# -*- coding: utf-8 -*-
"""询价算法，纯函数，无 IO。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median as statistics_median, quantiles
from typing import Iterable, List, Optional, Protocol


def mean(prices: List[float]) -> Optional[float]:
    """计算平均值，空列表返回 None。"""
    valid = [p for p in prices if p is not None and p > 0]
    if not valid:
        return None
    return sum(valid) / len(valid)


def remove_extreme_prices(
    prices: Iterable[Optional[float]],
) -> list[float]:
    """Return unique positive prices after Tukey-IQR outlier filtering.

    Fewer than four unique prices are kept as-is because there is not enough
    data to identify an extreme value reliably. Values outside the 1.5*IQR
    fences are removed; if no value falls outside the fences, nothing is
    removed.
    """
    unique_prices = sorted(
        {
            float(price)
            for price in prices
            if price is not None and price > 0
        }
    )
    if len(unique_prices) < 4:
        return unique_prices

    quartiles = quantiles(
        unique_prices,
        n=4,
        method="inclusive",
    )
    q1, q3 = quartiles[0], quartiles[2]
    iqr = q3 - q1
    if iqr <= 0:
        return unique_prices

    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr
    filtered = [
        price
        for price in unique_prices
        if lower_fence <= price <= upper_fence
    ]
    return filtered or unique_prices


def median(prices: Iterable[Optional[float]]) -> Optional[float]:
    """Deduplicate, remove IQR outliers, and return the median price."""
    cleaned = remove_extreme_prices(prices)
    return statistics_median(cleaned) if cleaned else None


@dataclass
class Decision:
    final_price: Optional[float]
    branch: str


@dataclass
class AlgorithmInput:
    quote_price_lists: list[list[float]]
    community_avg_prices: list[Optional[float]]
    deal_price_lists: list[list[float]]
    diff_threshold: float = 0.10
    no_deal_discount: float = 0.9
    quote_only_discount: float = 0.9


class AlgorithmStrategy(Protocol):
    def evaluate(self, inputs: AlgorithmInput) -> "AlgorithmEvaluation":
        ...


@dataclass
class AlgorithmEvaluation:
    quote_avg: Optional[float]
    deal_avg: Optional[float]
    decision: Decision


def aggregate_default_quote(
    quote_price_lists: Iterable[Iterable[float]],
    community_avg_prices: Iterable[Optional[float]],
) -> Optional[float]:
    """Aggregate the historical DEFAULT quote source per platform."""
    platform_quotes: list[float] = []
    for quote_prices, community_avg_price in zip(
        quote_price_lists,
        community_avg_prices,
    ):
        quote = community_avg_price or mean(list(quote_prices))
        if quote is not None and quote > 0:
            platform_quotes.append(quote)
    return mean(platform_quotes)


def aggregate_quote_only_prices(
    quote_price_lists: Iterable[Iterable[float]],
) -> Optional[float]:
    """Pool listings, remove duplicates/extremes, and return their median."""
    all_quote_prices: list[float] = []
    for quote_prices in quote_price_lists:
        all_quote_prices.extend(
            price for price in quote_prices if price is not None and price > 0
        )
    return median(all_quote_prices)


def aggregate_deal_prices(
    deal_price_lists: Iterable[Iterable[float]],
) -> Optional[float]:
    """Pool every deal price across successful platforms."""
    all_deal_prices: list[float] = []
    for deal_prices in deal_price_lists:
        all_deal_prices.extend(deal_prices)
    return mean(all_deal_prices)


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


def decide_quote_only(
    quote_avg: Optional[float],
    quote_discount: float = 0.9,
) -> Decision:
    """纯在售算法：聚合在售均价后打折输出，不依赖成交数据。

    - 有在售数据：quote_avg × quote_discount → 最终单价
    - 无在售数据 → FAILED
    """
    if quote_avg is None:
        return Decision(final_price=None, branch="FAILED")
    return Decision(
        final_price=quote_avg * quote_discount,
        branch="QUOTE_ONLY",
    )


class DefaultAlgorithm:
    """Historical transaction-plus-listing strategy."""

    def evaluate(self, inputs: AlgorithmInput) -> AlgorithmEvaluation:
        quote_avg = aggregate_default_quote(
            inputs.quote_price_lists,
            inputs.community_avg_prices,
        )
        deal_avg = aggregate_deal_prices(inputs.deal_price_lists)
        return AlgorithmEvaluation(
            quote_avg=quote_avg,
            deal_avg=deal_avg,
            decision=decide(
                quote_avg,
                deal_avg,
                inputs.diff_threshold,
                inputs.no_deal_discount,
            ),
        )


class QuoteOnlyAlgorithm:
    """Listing-only strategy that pools all listing prices."""

    def evaluate(self, inputs: AlgorithmInput) -> AlgorithmEvaluation:
        quote_avg = aggregate_quote_only_prices(inputs.quote_price_lists)
        return AlgorithmEvaluation(
            quote_avg=quote_avg,
            deal_avg=None,
            decision=decide_quote_only(
                quote_avg,
                inputs.quote_only_discount,
            ),
        )


ALGORITHM_REGISTRY: dict[str, AlgorithmStrategy] = {
    "default": DefaultAlgorithm(),
    "quote_only": QuoteOnlyAlgorithm(),
}


def get_algorithm_strategy(algorithm_mode: str) -> AlgorithmStrategy:
    """Resolve an algorithm mode, preserving DEFAULT fallback behavior."""
    return ALGORITHM_REGISTRY.get(algorithm_mode, ALGORITHM_REGISTRY["default"])


def evaluate_algorithm(
    algorithm_mode: str,
    inputs: AlgorithmInput,
) -> AlgorithmEvaluation:
    """Evaluate standard platform inputs through the selected strategy."""
    return get_algorithm_strategy(algorithm_mode).evaluate(inputs)
