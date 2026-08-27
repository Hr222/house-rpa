# -*- coding: utf-8 -*-
"""Listing selection and weak-reference rules for price estimation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Iterable, TypeVar

from app.algorithm.area_rules import listing_area_bounds
from app.algorithm.config import get_weak_area_max_tolerance
from app.algorithm.listing_dedup import deduplicate_same_platform
from app.algorithm.weighted_median import find_weighted_price_candidates


T = TypeVar("T")


@dataclass(frozen=True)
class ListingSelection(Generic[T]):
    """Algorithm-selected listing snapshots for one platform."""

    snapshots: tuple[T, ...]
    strict_snapshots: tuple[T, ...]
    quote_prices: tuple[float, ...]
    applied_tolerance: float
    reference_listing_count: int
    uses_weak_reference: bool


def _quote_prices(snapshots: Iterable[T]) -> list[float]:
    return [
        float(price)
        for snapshot in snapshots
        if (price := getattr(snapshot, "unit_price", None)) is not None and price > 0
    ]


def _filter_by_area(snapshots: Iterable[T], area: float, tolerance: float) -> list[T]:
    area_min, area_max = listing_area_bounds(area, tolerance)
    return [
        snapshot
        for snapshot in snapshots
        if (snapshot_area := getattr(snapshot, "area", None)) is not None
        and area_min <= snapshot_area <= area_max
    ]


def _has_effective_price_peak(snapshots: Iterable[T]) -> bool:
    prices = _quote_prices(snapshots)
    return bool(prices and find_weighted_price_candidates([prices]))


def select_listings_for_estimation(
    snapshots: Iterable[T],
    area: float,
    *,
    strict_tolerance: float = 1.0,
    max_tolerance: float | None = None,
) -> ListingSelection[T]:
    """Select strict-area listings, expanding only for an algorithm weak reference."""
    source = list(snapshots)
    strict = deduplicate_same_platform(
        _filter_by_area(source, area, strict_tolerance)
    )
    selected = strict
    applied_tolerance = strict_tolerance
    reference_listing_count = 0

    maximum = (
        get_weak_area_max_tolerance()
        if max_tolerance is None
        else float(max_tolerance)
    )
    if not _has_effective_price_peak(strict) and maximum > strict_tolerance:
        expanded = deduplicate_same_platform(_filter_by_area(source, area, maximum))
        if _has_effective_price_peak(expanded):
            strict_ids = {id(snapshot) for snapshot in strict}
            selected = expanded
            applied_tolerance = max(
                [
                    strict_tolerance,
                    *(
                        abs(snapshot.area - area)
                        for snapshot in expanded
                        if getattr(snapshot, "area", None) is not None
                    ),
                ]
            )
            reference_listing_count = sum(
                1
                for snapshot in expanded
                if id(snapshot) not in strict_ids
                and (price := getattr(snapshot, "unit_price", None)) is not None
                and price > 0
            )

    prices = _quote_prices(selected)
    uses_weak_reference = bool(
        prices and (len(prices) == 1 or reference_listing_count > 0)
    )
    return ListingSelection(
        snapshots=tuple(selected),
        strict_snapshots=tuple(strict),
        quote_prices=tuple(prices),
        applied_tolerance=applied_tolerance,
        reference_listing_count=1 if len(prices) == 1 else reference_listing_count,
        uses_weak_reference=uses_weak_reference,
    )
