# -*- coding: utf-8 -*-
"""Conservative same-platform listing deduplication helpers."""

from __future__ import annotations

import re
from typing import Any, Iterable, TypeVar


T = TypeVar("T")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def listing_dedup_key(item: T) -> tuple[Any, ...] | None:
    """Build a key for a definite duplicate within one platform."""
    house_id = _text(getattr(item, "house_id", ""))
    if house_id:
        return ("house_id", house_id)

    community = _text(getattr(item, "community_name", ""))
    title = _text(getattr(item, "title", ""))
    layout = _text(getattr(item, "layout", ""))
    area = _number(getattr(item, "area", None))
    unit_price = _number(getattr(item, "unit_price", None))
    total_price = _number(getattr(item, "total_price", None))
    if not community or not title or not layout:
        return None
    if area is None or unit_price is None or total_price is None:
        return None
    return (
        "fields",
        community,
        title,
        area,
        layout,
        unit_price,
        total_price,
    )


def deduplicate_same_platform(items: Iterable[T]) -> list[T]:
    """Keep the first occurrence of definite duplicates in input order."""
    result: list[T] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        key = listing_dedup_key(item)
        if key is None or key not in seen:
            result.append(item)
        if key is not None:
            seen.add(key)
    return result
