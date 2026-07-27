# -*- coding: utf-8 -*-
"""询价算法，纯函数，无 IO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isclose
from statistics import median as statistics_median, quantiles
from typing import Iterable, List, Optional, Protocol

from app.core.models import PriceCandidate


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
    candidates: list[PriceCandidate] = field(default_factory=list)


WEIGHTED_MEDIAN_MIN_COVERAGE = 0.60
WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION = 0.10


@dataclass(frozen=True)
class _WeightedPrice:
    price: float
    weight: float


@dataclass(frozen=True)
class _WeightedInterval:
    start: int
    end: int
    weight: float
    max_relative_deviation: float


@dataclass(frozen=True)
class WeightedMedianDiagnostic:
    """Explain the best candidate cluster for user-facing diagnostics."""

    prices: tuple[float, ...]
    center: float
    coverage: float
    max_relative_deviation: float


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


def _weighted_median(
    prices: Iterable[_WeightedPrice],
) -> Optional[float]:
    """Return the weighted median of positive prices."""
    ordered = sorted(prices, key=lambda item: item.price)
    if not ordered:
        return None

    total_weight = sum(item.weight for item in ordered)
    if total_weight <= 0:
        return None

    half_weight = total_weight / 2
    cumulative_weight = 0.0
    for index, item in enumerate(ordered):
        cumulative_weight += item.weight
        if isclose(cumulative_weight, half_weight, rel_tol=1e-12, abs_tol=1e-12):
            if index + 1 < len(ordered):
                return (item.price + ordered[index + 1].price) / 2
            return item.price
        if cumulative_weight > half_weight:
            return item.price
    return ordered[-1].price


def _build_platform_weighted_prices(
    quote_price_lists: Iterable[Iterable[float]],
) -> list[_WeightedPrice]:
    """Use one vote per valid listing; frequency is the business signal."""
    weighted_prices: list[_WeightedPrice] = []
    for quote_prices in quote_price_lists:
        valid_prices = [
            float(price)
            for price in quote_prices
            if price is not None and price > 0
        ]
        if not valid_prices:
            continue

        weighted_prices.extend(
            _WeightedPrice(
                price=price,
                weight=1.0,
            )
            for price in valid_prices
        )
    return sorted(weighted_prices, key=lambda item: item.price)


def _within_relative_deviation(price: float, center: float, limit: float) -> bool:
    return center > 0 and abs(price - center) / center <= limit


def _mode_members(
    prices: list[float],
    seed: float,
    max_relative_deviation: float,
) -> tuple[float, list[float]]:
    """Find a local price mode and refine its center once."""
    members = [
        price
        for price in prices
        if _within_relative_deviation(price, seed, max_relative_deviation)
    ]
    if not members:
        return seed, []
    center = statistics_median(members)
    members = [
        price
        for price in prices
        if _within_relative_deviation(price, center, max_relative_deviation)
    ]
    return statistics_median(members) if members else center, members


def _find_price_modes(
    prices: list[float],
    max_relative_deviation: float = WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
) -> list[tuple[float, list[float]]]:
    """Find frequency peaks without merging separated price populations."""
    if not prices:
        return []

    ranked: list[tuple[int, float]] = []
    for seed in sorted(set(prices)):
        center, members = _mode_members(prices, seed, max_relative_deviation)
        if members:
            ranked.append((len(members), center))

    selected: list[tuple[float, list[float]]] = []
    suppression_limit = max_relative_deviation * 2
    for _, center in sorted(ranked, key=lambda item: (-item[0], item[1])):
        if any(
            abs(center - selected_center) / min(center, selected_center)
            <= suppression_limit
            for selected_center, _ in selected
        ):
            continue
        refined_center, members = _mode_members(
            prices,
            center,
            max_relative_deviation,
        )
        if members:
            selected.append((refined_center, members))

    return sorted(selected, key=lambda item: item[0])


def find_weighted_price_candidates(
    quote_price_lists: Iterable[Iterable[float]],
    max_relative_deviation: float = WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
    quote_discount: float = 0.9,
) -> list[PriceCandidate]:
    """Return significant listing-price peaks in ascending price order.

    A small isolated peak is treated as an outlier only when it occurs less
    than 60% as often as the strongest peak. A low price that appears often is
    therefore retained as a real market segment instead of being discarded
    merely because it is numerically extreme.
    """
    prices = [
        item.price
        for item in _build_platform_weighted_prices(quote_price_lists)
    ]
    if not prices:
        return []

    modes = _find_price_modes(prices, max_relative_deviation)
    if not modes:
        return []

    max_count = max(len(members) for _, members in modes)
    min_count = (
        1
        if max_count == 1
        else max(2, int(max_count * 0.60 + 0.999999))
    )
    significant = [
        (center, members)
        for center, members in modes
        if len(members) >= min_count
    ]
    total_count = len(prices)
    return [
        PriceCandidate(
            quote_price=statistics_median(members),
            final_price=statistics_median(members) * quote_discount,
            count=len(members),
            frequency=len(members) / total_count,
            min_price=min(members),
            max_price=max(members),
        )
        for _, members in significant
    ]


def _find_narrowest_weighted_interval(
    prices: list[_WeightedPrice],
    min_coverage: float = WEIGHTED_MEDIAN_MIN_COVERAGE,
    max_relative_deviation: float = WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
) -> Optional[_WeightedInterval]:
    """Find a dense interval whose individual prices stay near its center."""
    if (
        not prices
        or not 0 < min_coverage <= 1
        or max_relative_deviation <= 0
    ):
        return None

    total_weight = sum(item.weight for item in prices)
    target_weight = total_weight * min_coverage
    candidates: list[_WeightedInterval] = []

    for start in range(len(prices)):
        interval_weight = 0.0
        for end in range(start, len(prices)):
            interval_weight += prices[end].weight
            if interval_weight < target_weight:
                continue

            interval_prices = prices[start : end + 1]
            center = _weighted_median(interval_prices)
            if center is None or center <= 0:
                break

            max_relative_deviation_in_interval = max(
                abs(item.price - center) / center
                for item in interval_prices
            )
            candidates.append(
                _WeightedInterval(
                    start=start,
                    end=end,
                    weight=interval_weight,
                    max_relative_deviation=max_relative_deviation_in_interval,
                )
            )
            break

    if not candidates:
        return None

    best = min(
        candidates,
        key=lambda interval: (
            interval.max_relative_deviation,
            -interval.weight,
            interval.end - interval.start,
        ),
    )
    return best if best.max_relative_deviation <= max_relative_deviation else None


def aggregate_weighted_median_quote(
    quote_price_lists: Iterable[Iterable[float]],
    min_coverage: float = WEIGHTED_MEDIAN_MIN_COVERAGE,
    max_relative_deviation: float = WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
) -> Optional[float]:
    """Return a median only when one significant price peak is clear."""
    candidates = find_weighted_price_candidates(
        quote_price_lists,
        max_relative_deviation=max_relative_deviation,
    )
    return candidates[0].quote_price if len(candidates) == 1 else None


def diagnose_weighted_median_quote(
    quote_price_lists: Iterable[Iterable[float]],
    min_coverage: float = WEIGHTED_MEDIAN_MIN_COVERAGE,
    max_relative_deviation: float = WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
) -> Optional[WeightedMedianDiagnostic]:
    """Return the candidate cluster details used to explain a no-data result.

    The diagnostic first finds the widest cluster whose individual prices meet
    the deviation limit. If that cluster still covers less than the required
    weight, it is useful evidence that the data is present but insufficiently
    concentrated. Otherwise it returns the closest cluster that reaches the
    coverage target and reports which deviation requirement it misses.
    """
    weighted_prices = _build_platform_weighted_prices(quote_price_lists)
    if not weighted_prices or not 0 < min_coverage <= 1 or max_relative_deviation <= 0:
        return None

    total_weight = sum(item.weight for item in weighted_prices)
    target_weight = total_weight * min_coverage
    best_valid: Optional[tuple[float, float, int, int, int, float]] = None

    for start in range(len(weighted_prices)):
        interval_weight = 0.0
        for end in range(start, len(weighted_prices)):
            interval_weight += weighted_prices[end].weight
            interval_prices = weighted_prices[start : end + 1]
            center = _weighted_median(interval_prices)
            if center is None or center <= 0:
                continue
            max_deviation = max(
                abs(item.price - center) / center
                for item in interval_prices
            )
            if max_deviation > max_relative_deviation:
                continue

            candidate = (
                interval_weight,
                -max_deviation,
                -(end - start),
                start,
                end,
                center,
            )
            if best_valid is None or candidate > best_valid:
                best_valid = candidate

    if best_valid is not None:
        interval_weight, negative_deviation, _, start, end, center = best_valid
        if interval_weight < target_weight:
            selected = weighted_prices[start : end + 1]
            return WeightedMedianDiagnostic(
                prices=tuple(item.price for item in selected),
                center=center,
                coverage=interval_weight / total_weight,
                max_relative_deviation=-negative_deviation,
            )

    closest = _find_narrowest_weighted_interval(
        weighted_prices,
        min_coverage,
        float("inf"),
    )
    if closest is None:
        return None
    selected = weighted_prices[closest.start : closest.end + 1]
    center = _weighted_median(selected)
    if center is None:
        return None
    return WeightedMedianDiagnostic(
        prices=tuple(item.price for item in selected),
        center=center,
        coverage=closest.weight / total_weight,
        max_relative_deviation=max(
            abs(item.price - center) / center
            for item in selected
        ),
    )


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


def decide_weighted_median(
    quote_avg: Optional[float],
    quote_discount: float = 0.9,
) -> Decision:
    """Apply the listing discount to a weighted-median quote result."""
    if quote_avg is None:
        return Decision(final_price=None, branch="FAILED")
    return Decision(
        final_price=quote_avg * quote_discount,
        branch="WEIGHTED_MEDIAN",
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


class WeightedMedianAlgorithm:
    """Listing strategy based on frequency-ranked price modes."""

    def evaluate(self, inputs: AlgorithmInput) -> AlgorithmEvaluation:
        candidates = find_weighted_price_candidates(
            inputs.quote_price_lists,
            quote_discount=inputs.quote_only_discount,
        )
        if len(candidates) > 1:
            selected = min(candidates, key=lambda candidate: candidate.quote_price)
            candidates = [
                PriceCandidate(
                    quote_price=candidate.quote_price,
                    final_price=candidate.quote_price,
                    count=candidate.count,
                    frequency=candidate.frequency,
                    min_price=candidate.min_price,
                    max_price=candidate.max_price,
                )
                for candidate in candidates
            ]
            decision = Decision(
                final_price=selected.quote_price,
                branch="WEIGHTED_MEDIAN_MULTI",
            )
            quote_avg = selected.quote_price
        else:
            quote_avg = candidates[0].quote_price if candidates else None
            decision = decide_weighted_median(
                quote_avg,
                inputs.quote_only_discount,
            )
        return AlgorithmEvaluation(
            quote_avg=quote_avg,
            deal_avg=None,
            decision=decision,
            candidates=candidates,
        )


ALGORITHM_REGISTRY: dict[str, AlgorithmStrategy] = {
    "default": DefaultAlgorithm(),
    "quote_only": QuoteOnlyAlgorithm(),
    "weighted_median": WeightedMedianAlgorithm(),
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
