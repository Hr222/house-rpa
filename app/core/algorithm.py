# -*- coding: utf-8 -*-
"""询价算法，纯函数，无 IO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isclose
from statistics import median as statistics_median
from typing import Iterable, Optional, Protocol

from app.core.models import PriceCandidate


@dataclass
class Decision:
    final_price: Optional[float]
    branch: str


@dataclass
class AlgorithmInput:
    quote_price_lists: list[list[float]]
    weighted_median_discount: float = 0.9


class AlgorithmStrategy(Protocol):
    """Stable extension point for future algorithm implementations."""

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


class WeightedMedianAlgorithm:
    """Listing strategy based on frequency-ranked price modes."""

    def evaluate(self, inputs: AlgorithmInput) -> AlgorithmEvaluation:
        candidates = find_weighted_price_candidates(
            inputs.quote_price_lists,
            quote_discount=inputs.weighted_median_discount,
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
                inputs.weighted_median_discount,
            )
        return AlgorithmEvaluation(
            quote_avg=quote_avg,
            deal_avg=None,
            decision=decision,
            candidates=candidates,
        )


_WEIGHTED_MEDIAN_ALGORITHM = WeightedMedianAlgorithm()

ALGORITHM_REGISTRY: dict[str, AlgorithmStrategy] = {
    "DEFAULT": _WEIGHTED_MEDIAN_ALGORITHM,
}


def get_algorithm_strategy(algorithm_mode: str = "DEFAULT") -> AlgorithmStrategy:
    """Resolve the registered strategy while keeping DEFAULT as the fallback."""
    mode = str(algorithm_mode or "DEFAULT").upper()
    return ALGORITHM_REGISTRY.get(mode, ALGORITHM_REGISTRY["DEFAULT"])


def evaluate_algorithm(
    inputs: AlgorithmInput | str,
    maybe_inputs: Optional[AlgorithmInput] = None,
    *,
    algorithm_mode: Optional[str] = None,
) -> AlgorithmEvaluation:
    """Evaluate through the registered strategy, defaulting to DEFAULT.

    ``inputs=...`` is the current service-facing form. The optional legacy
    positional form ``evaluate_algorithm(mode, inputs)`` remains useful for
    the strategy extension point, while the HTTP API no longer exposes it.
    """
    if isinstance(inputs, AlgorithmInput):
        selected_inputs = inputs
        selected_mode = algorithm_mode or "DEFAULT"
    else:
        if maybe_inputs is None:
            raise TypeError("evaluate_algorithm requires AlgorithmInput")
        selected_inputs = maybe_inputs
        selected_mode = algorithm_mode or inputs
    return get_algorithm_strategy(selected_mode).evaluate(selected_inputs)
