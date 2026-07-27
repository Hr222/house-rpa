# -*- coding: utf-8 -*-
"""Conservative same- and cross-platform listing deduplication helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Generic, Iterable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ListingDuplicateGroup(Generic[T]):
    """A high-confidence cross-platform duplicate group."""

    members: tuple[T, ...]
    reason: str

    @property
    def representative(self) -> T:
        return self.members[0]


@dataclass(frozen=True)
class ListingDeduplicationResult(Generic[T]):
    """Result of the two-stage listing de-duplication process."""

    same_platform_items: tuple[T, ...]
    items: tuple[T, ...]
    cross_platform_groups: tuple[ListingDuplicateGroup[T], ...]
    raw_count: int

    @property
    def same_platform_removed(self) -> int:
        return self.raw_count - len(self.same_platform_items)

    @property
    def cross_platform_removed(self) -> int:
        return len(self.same_platform_items) - len(self.items)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _normalized_text(value: Any) -> str:
    """Normalize harmless punctuation/spacing differences between sites."""
    text = _text(value).casefold()
    punctuation = r"[\s,\uFF0C\u3002.!\uFF01\u3001;\uFF1B:/\\|()\uFF08\uFF09\[\]\u3010\u3011_-]+"
    return re.sub(punctuation, "", text)


def _layout_signature(value: Any) -> tuple[int, int] | None:
    """Extract room/hall counts from Chinese or English layout text."""
    text = _text(value).casefold()
    if not text:
        return None

    room_match = re.search(
        r"(\d+)\s*(?:\u5ba4|\u623f|\u5c45\u5ba4|room|rooms)",
        text,
    )
    hall_match = re.search(r"(\d+)\s*(?:\u5385|hall|halls)", text)
    if room_match is None or hall_match is None:
        return None
    return int(room_match.group(1)), int(hall_match.group(1))


def _titles_match(left: Any, right: Any) -> bool:
    """Match titles only after harmless spacing/punctuation normalization."""
    left_title = _normalized_text(left)
    right_title = _normalized_text(right)
    if not left_title or not right_title:
        return False
    return left_title == right_title


def listing_dedup_key(item: T) -> tuple[Any, ...] | None:
    """Build a key for a definite duplicate within one platform."""
    platform = _text(getattr(item, "platform", ""))
    platform_prefix: tuple[Any, ...] = ("platform", platform) if platform else ()
    house_id = _text(getattr(item, "house_id", ""))
    if house_id:
        return platform_prefix + ("house_id", house_id)

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
    return platform_prefix + (
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


def _cross_platform_match(
    left: T,
    right: T,
    *,
    area_tolerance: float,
    unit_price_tolerance: float,
) -> bool:
    """Return whether two rows are safe to treat as one listing."""
    left_platform = _normalized_text(getattr(left, "platform", ""))
    right_platform = _normalized_text(getattr(right, "platform", ""))
    if not left_platform or left_platform == right_platform:
        return False

    left_community = _normalized_text(getattr(left, "community_name", ""))
    right_community = _normalized_text(getattr(right, "community_name", ""))
    if not left_community or left_community != right_community:
        return False

    left_area = _number(getattr(left, "area", None))
    right_area = _number(getattr(right, "area", None))
    left_price = _number(getattr(left, "unit_price", None))
    right_price = _number(getattr(right, "unit_price", None))
    if left_area is None or right_area is None or left_price is None or right_price is None:
        return False
    if abs(left_area - right_area) > area_tolerance:
        return False
    if abs(left_price - right_price) > unit_price_tolerance:
        return False

    left_layout = _layout_signature(getattr(left, "layout", ""))
    right_layout = _layout_signature(getattr(right, "layout", ""))
    if left_layout is not None and right_layout is not None:
        return left_layout == right_layout

    # When either feed omits layout, use the normalized title as the fallback
    # identity signal. This covers one missing layout and both missing layouts.
    return _titles_match(
        getattr(left, "title", ""),
        getattr(right, "title", ""),
    )


def deduplicate_cross_platform(
    items: Iterable[T],
    *,
    area_tolerance: float = 0.5,
    unit_price_tolerance: float = 100.0,
) -> tuple[list[T], list[ListingDuplicateGroup[T]]]:
    """Collapse only unambiguous duplicate groups spanning platforms."""
    rows = list(items)
    matched_indexes: set[int] = set()
    representative_indexes: set[int] = set()
    groups: list[ListingDuplicateGroup[T]] = []
    components: list[list[int]] = []

    # Compare each row with the group's first row only. This keeps the
    # representative as the identity anchor and avoids treating two rows as
    # directly equal merely because they happen to share another match.
    for index, row in enumerate(rows):
        compatible_components = []
        for component_index, component in enumerate(components):
            platform = _normalized_text(getattr(row, "platform", ""))
            component_platforms = {
                _normalized_text(getattr(rows[item], "platform", ""))
                for item in component
            }
            if platform and platform in component_platforms:
                continue
            if _cross_platform_match(
                row,
                rows[component[0]],
                area_tolerance=area_tolerance,
                unit_price_tolerance=unit_price_tolerance,
            ):
                compatible_components.append(component_index)

        # Ambiguous matches are retained as separate listings.
        if len(compatible_components) != 1:
            components.append([index])
            continue
        components[compatible_components[0]].append(index)

    for component in components:
        if len(component) < 2:
            continue
        members = tuple(rows[index] for index in component)
        has_layout = all(
            _layout_signature(getattr(item, "layout", "")) is not None
            for item in members
        )
        reason = (
            "\u5c0f\u533a\u4e00\u81f4\uff0c\u62a5\u4ef7\u5dee\u2264100\u5143/\u5e73\uff0c\u9762\u79ef\u5dee\u22640.5\u33a1\uff0c\u6237\u578b\u4e00\u81f4\uff0c\u6807\u9898\u5dee\u5f02\u4e0d\u5f71\u54cd\u5224\u5b9a"
            if has_layout
            else (
                "\u5c0f\u533a\u4e00\u81f4\uff0c\u62a5\u4ef7\u5dee\u2264100\u5143/\u5e73\uff0c\u9762\u79ef\u5dee\u22640.5\u33a1\uff0c"
                "\u6237\u578b\u6709\u7f3a\u5931\uff0c\u6807\u9898\u6807\u51c6\u5316\u540e\u4e0e\u4ee3\u8868\u623f\u6e90\u4e00\u81f4"
            )
        )
        groups.append(
            ListingDuplicateGroup(
                members=members,
                reason=reason,
            )
        )
        matched_indexes.update(component)
        representative_indexes.add(component[0])

    kept = [
        row
        for index, row in enumerate(rows)
        if index not in matched_indexes or index in representative_indexes
    ]
    return kept, groups


def deduplicate_listings(items: Iterable[T]) -> ListingDeduplicationResult[T]:
    """Apply same-platform then conservative cross-platform de-duplication."""
    raw_items = list(items)
    same_platform_items = deduplicate_same_platform(raw_items)
    cross_platform_items, groups = deduplicate_cross_platform(same_platform_items)
    return ListingDeduplicationResult(
        same_platform_items=tuple(same_platform_items),
        items=tuple(cross_platform_items),
        cross_platform_groups=tuple(groups),
        raw_count=len(raw_items),
    )
