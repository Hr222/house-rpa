# -*- coding: utf-8 -*-
"""Shared area ranges used by price-estimation rules."""

from __future__ import annotations


LISTING_AREA_TOLERANCE = 1.0
DEAL_AREA_TOLERANCE = 5.0


def listing_area_bounds(
    area: float,
    tolerance: float = LISTING_AREA_TOLERANCE,
) -> tuple[float, float]:
    """Return the comparable listing area range, defaulting to request area ±1㎡."""
    return area - tolerance, area + tolerance


def deal_area_bounds(
    area: float,
    tolerance: float = DEAL_AREA_TOLERANCE,
) -> tuple[float, float]:
    """Return the comparable real-deal area range, defaulting to request area ±5㎡."""
    return listing_area_bounds(area, tolerance)
