# -*- coding: utf-8 -*-
"""询价算法，纯函数，无 IO。"""

from __future__ import annotations

from collections import Counter
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
    deal_price_lists: list[list[float]] = field(default_factory=list)
    area: Optional[float] = None
    luxury_data_sparse: bool = False


class AlgorithmStrategy(Protocol):
    """为未来算法实现保留的稳定扩展点。"""

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

LUXURY_TRANSITION_START_AREA = 120.0
LUXURY_AREA_START = 125.0
LUXURY_SINGLE_EXTRA_DISCOUNTS = (
    (150.0, 0.05),
    (200.0, 0.05),
    (250.0, 0.17),
    (300.0, 0.10),
    (float("inf"), 0.08),
)
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
    """解释最佳候选价格簇，供用户侧诊断使用。"""

    prices: tuple[float, ...]
    center: float
    coverage: float
    max_relative_deviation: float


def _weighted_median(
    prices: Iterable[_WeightedPrice],
) -> Optional[float]:
    """返回正数价格的加权中位数。"""
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
    """每条有效房源计一票；频次是业务信号。"""
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
    """查找局部价格众数，并对其中心值进行一次修正。"""
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
    """查找频次峰值，不合并彼此分离的价格群体。"""
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

    # 非极大值抑制会合并相邻的重叠窗口。若被合并的窗口仍包含至少两条未被
    # 已选峰覆盖的报价，它具有独立的密集核心，必须恢复为单独价格峰。
    selected_member_keys = {tuple(sorted(members)) for _, members in selected}
    covered_members: Counter[float] = Counter()
    for _, members in selected:
        covered_members |= Counter(members)

    for _, center in sorted(ranked, key=lambda item: (-item[0], item[1])):
        refined_center, members = _mode_members(
            prices,
            center,
            max_relative_deviation,
        )
        member_key = tuple(sorted(members))
        if len(members) <= 1 or member_key in selected_member_keys:
            continue
        if any(
            abs(refined_center - selected_center)
            / min(refined_center, selected_center)
            <= max_relative_deviation
            for selected_center, _ in selected
        ):
            continue
        uncovered_count = sum((Counter(members) - covered_members).values())
        if uncovered_count < 2:
            continue
        selected.append((refined_center, members))
        selected_member_keys.add(member_key)
        covered_members |= Counter(members)

    return sorted(selected, key=lambda item: item[0])


def find_weighted_price_candidates(
    quote_price_lists: Iterable[Iterable[float]],
    max_relative_deviation: float = WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
    quote_discount: float = 0.9,
) -> list[PriceCandidate]:
    """按价格升序返回显著的挂牌单价峰值。

    仅剔除未能形成自身价格簇的孤立点；与主峰频次相差较大的密集价格簇仍是独立峰，
    不会仅因频次或数值偏低而被丢弃。
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

    dense_modes = [
        (center, members)
        for center, members in modes
        if len(members) > 1
    ]
    significant = dense_modes or modes
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
    """查找一个密集区间，使其中每条价格都接近该区间的中心值。"""
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
    """仅在存在一个明确的显著价格峰值时返回中位数。"""
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
    """返回用于解释无数据结果的候选价格簇详情。

    诊断过程先查找每条价格都满足偏差限制的最宽价格簇。如果该价格簇覆盖的权重仍低于
    要求，则说明数据存在但集中度不足。否则返回达到覆盖目标的最接近价格簇，并报告其
    未满足的偏差要求。
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
    """对加权落点中位数询价结果应用挂牌折扣。"""
    if quote_avg is None:
        return Decision(final_price=None, branch="FAILED")
    return Decision(
        final_price=quote_avg * quote_discount,
        branch="WEIGHTED_MEDIAN",
    )


def _luxury_transition_weight(area: Optional[float]) -> float:
    """Return the gradual luxury weight for the 120-125㎡ transition zone."""
    if area is None or area < LUXURY_TRANSITION_START_AREA:
        return 0.0
    if area >= LUXURY_AREA_START:
        return 1.0
    return (area - LUXURY_TRANSITION_START_AREA) / (
        LUXURY_AREA_START - LUXURY_TRANSITION_START_AREA
    )


def _luxury_discount_rate(
    area: Optional[float],
    bands: tuple[tuple[float, float], ...],
) -> float:
    """Return an area-band discount, blended at the 120-125㎡ boundary."""
    weight = _luxury_transition_weight(area)
    if weight <= 0.0:
        return 0.0
    assert area is not None
    full_rate = next(rate for upper, rate in bands if area <= upper)
    return full_rate * weight


def _luxury_single_multiplier(area: Optional[float]) -> float:
    return 1.0 - _luxury_discount_rate(area, LUXURY_SINGLE_EXTRA_DISCOUNTS)


def _select_deal_price(
    deal_price_lists: Iterable[Iterable[float]],
    max_relative_deviation: float = WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
) -> Optional[float]:
    """选择目标面积成交价，不应用挂牌折扣。"""
    valid_prices = [
        float(price)
        for price_list in deal_price_lists
        for price in price_list
        if price is not None and price > 0
    ]
    if not valid_prices:
        return None
    if len(valid_prices) == 1:
        return valid_prices[0]

    candidates = find_weighted_price_candidates(
        [valid_prices],
        max_relative_deviation=max_relative_deviation,
        quote_discount=1.0,
    )
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate.quote_price).quote_price


class WeightedMedianAlgorithm:
    """基于频次排序价格众数的挂牌价策略。"""

    def evaluate(self, inputs: AlgorithmInput) -> AlgorithmEvaluation:
        candidates = find_weighted_price_candidates(
            inputs.quote_price_lists,
            quote_discount=inputs.weighted_median_discount,
        )
        deal_avg = _select_deal_price(inputs.deal_price_lists)
        luxury_single_adjustment_enabled = (
            inputs.luxury_data_sparse and deal_avg is None
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
                # A multi-peak decision already selects the lowest valid peak.
                # Do not apply a second luxury discount to that conservative value.
                final_price=selected.quote_price,
                branch="WEIGHTED_MEDIAN_MULTI",
            )
            quote_avg = selected.quote_price
        else:
            quote_avg = candidates[0].quote_price if candidates else None
            luxury_multiplier = (
                _luxury_single_multiplier(inputs.area)
                if luxury_single_adjustment_enabled
                else 1.0
            )
            decision = decide_weighted_median(
                quote_avg,
                inputs.weighted_median_discount * luxury_multiplier,
            )
            if candidates and luxury_single_adjustment_enabled:
                candidates = [
                    PriceCandidate(
                        quote_price=candidate.quote_price,
                        final_price=candidate.final_price * luxury_multiplier,
                        count=candidate.count,
                        frequency=candidate.frequency,
                        min_price=candidate.min_price,
                        max_price=candidate.max_price,
                    )
                    for candidate in candidates
                ]
        if decision.final_price is not None and deal_avg is not None:
            decision = Decision(
                # 有真实成交价时，挂牌峰值不打折，直接与成交价等权平均。
                final_price=(quote_avg + deal_avg) / 2,
                branch="WEIGHTED_MEDIAN_COMBINED",
            )

        return AlgorithmEvaluation(
            quote_avg=quote_avg,
            deal_avg=deal_avg,
            decision=decision,
            candidates=candidates,
        )


_WEIGHTED_MEDIAN_ALGORITHM = WeightedMedianAlgorithm()

ALGORITHM_REGISTRY: dict[str, AlgorithmStrategy] = {
    "DEFAULT": _WEIGHTED_MEDIAN_ALGORITHM,
}


def get_algorithm_strategy(algorithm_mode: str = "DEFAULT") -> AlgorithmStrategy:
    """解析已注册的策略，并将 DEFAULT 作为兜底策略。"""
    mode = str(algorithm_mode or "DEFAULT").upper()
    return ALGORITHM_REGISTRY.get(mode, ALGORITHM_REGISTRY["DEFAULT"])


def evaluate_algorithm(
    inputs: AlgorithmInput | str,
    maybe_inputs: Optional[AlgorithmInput] = None,
    *,
    algorithm_mode: Optional[str] = None,
) -> AlgorithmEvaluation:
    """通过已注册的策略执行评估，默认使用 DEFAULT。

    ``inputs=...`` 是当前面向服务层的调用形式。可选的旧版位置参数形式
    ``evaluate_algorithm(mode, inputs)`` 仍可用于策略扩展点，但 HTTP API 已不再暴露该形式。
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
