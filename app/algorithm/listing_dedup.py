# -*- coding: utf-8 -*-
"""估价使用的保守房源去重。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Generic, Iterable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ListingDuplicateGroup(Generic[T]):
    """高置信度的跨平台重复组。"""

    members: tuple[T, ...]
    reason: str

    @property
    def representative(self) -> T:
        return self.members[0]


@dataclass(frozen=True)
class ListingDeduplicationResult(Generic[T]):
    """同平台与跨平台去重的结果。"""

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
    text = _text(value).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace()
        and unicodedata.category(character)[0] not in {"P", "S"}
    )


def _normalized_community(value: Any) -> str:
    text = _text(value)
    text = re.sub(r"[（(][^（）()]*[）)]$", "", text)
    return _normalized_text(text)


_RESIDENTIAL_COMMUNITY_SUFFIXES = (
    "山庄",
    "花园",
    "公馆",
    "家园",
    "华庭",
    "小区",
    "名苑",
)


def _communities_share_distinctive_name(left: Any, right: Any) -> bool:
    left_community = _normalized_community(left)
    right_community = _normalized_community(right)
    if not left_community or not right_community:
        return False
    if left_community == right_community:
        return True
    shorter, longer = sorted((left_community, right_community), key=len)
    return len(shorter) >= 3 and shorter in longer


def _layout_signature(value: Any) -> tuple[int, int] | None:
    text = _text(value).casefold()
    if not text:
        return None
    room_match = re.search(r"(\d+)\s*(?:室|房|居室|room|rooms)", text)
    hall_match = re.search(r"(\d+)\s*(?:厅|hall|halls)", text)
    if room_match is None or hall_match is None:
        return None
    return int(room_match.group(1)), int(hall_match.group(1))


def listing_dedup_key(item: T) -> tuple[Any, ...] | None:
    """构建稳定的同平台去重键。"""
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


def _same_platform_incomplete_match(left: T, right: T) -> bool:
    if _normalized_text(getattr(left, "platform", "")) != _normalized_text(
        getattr(right, "platform", "")
    ):
        return False
    if _text(getattr(left, "house_id", "")) or _text(getattr(right, "house_id", "")):
        return False
    if not _communities_share_distinctive_name(
        getattr(left, "community_name", ""),
        getattr(right, "community_name", ""),
    ):
        return False

    left_layout = _layout_signature(getattr(left, "layout", ""))
    right_layout = _layout_signature(getattr(right, "layout", ""))
    if left_layout is not None and right_layout is not None:
        return False

    left_area = _number(getattr(left, "area", None))
    right_area = _number(getattr(right, "area", None))
    left_price = _number(getattr(left, "unit_price", None))
    right_price = _number(getattr(right, "unit_price", None))
    left_total = _number(getattr(left, "total_price", None))
    right_total = _number(getattr(right, "total_price", None))
    return (
        left_area is not None
        and left_area == right_area
        and left_price is not None
        and left_price == right_price
        and left_total is not None
        and left_total == right_total
    )


def _listing_information_score(item: T) -> tuple[int, int, int]:
    return (
        int(_layout_signature(getattr(item, "layout", "")) is not None),
        len(_normalized_text(getattr(item, "title", ""))),
        len(_normalized_community(getattr(item, "community_name", ""))),
    )


def deduplicate_same_platform(items: Iterable[T]) -> list[T]:
    """按稳定标识与强字段匹配去重。"""
    result: list[T] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        key = listing_dedup_key(item)
        if key is not None:
            if key not in seen:
                result.append(item)
                seen.add(key)
            continue

        duplicate_index = next(
            (
                index
                for index, existing in enumerate(result)
                if _same_platform_incomplete_match(item, existing)
            ),
            None,
        )
        if duplicate_index is None:
            result.append(item)
        elif _listing_information_score(item) > _listing_information_score(
            result[duplicate_index]
        ):
            result[duplicate_index] = item
    return result


def _cross_platform_match_score(left: T, right: T) -> tuple[int, float, float, float] | None:
    left_platform = _normalized_text(getattr(left, "platform", ""))
    right_platform = _normalized_text(getattr(right, "platform", ""))
    if not left_platform or left_platform == right_platform:
        return None
    if not _communities_share_distinctive_name(
        getattr(left, "community_name", ""),
        getattr(right, "community_name", ""),
    ):
        return None

    left_area = _number(getattr(left, "area", None))
    right_area = _number(getattr(right, "area", None))
    left_price = _number(getattr(left, "unit_price", None))
    right_price = _number(getattr(right, "unit_price", None))
    if left_area is None or right_area is None or left_price is None or right_price is None:
        return None
    left_layout = _layout_signature(getattr(left, "layout", ""))
    right_layout = _layout_signature(getattr(right, "layout", ""))
    if left_layout is not None and right_layout is not None and left_layout != right_layout:
        return None

    left_total = _number(getattr(left, "total_price", None))
    right_total = _number(getattr(right, "total_price", None))
    if (
        left_area != right_area
        or left_price != right_price
        or left_total is None
        or left_total != right_total
    ):
        return None
    return int(left_layout is not None and right_layout is not None), 0.0, 0.0, 0.0


def deduplicate_cross_platform(items: Iterable[T]) -> tuple[list[T], list[ListingDuplicateGroup[T]]]:
    """仅合并跨平台无歧义的记录。"""
    rows = list(items)
    matched_indexes: set[int] = set()
    representative_indexes: set[int] = set()
    groups: list[ListingDuplicateGroup[T]] = []
    components: list[list[int]] = []

    for index, row in enumerate(rows):
        compatible_components: list[tuple[int, tuple[int, float, float, float]]] = []
        for component_index, component in enumerate(components):
            platform = _normalized_text(getattr(row, "platform", ""))
            component_platforms = {
                _normalized_text(getattr(rows[item], "platform", ""))
                for item in component
            }
            if platform and platform in component_platforms:
                continue
            score = _cross_platform_match_score(row, rows[component[0]])
            if score is not None:
                compatible_components.append((component_index, score))

        if not compatible_components:
            components.append([index])
            continue

        best_score = max(score for _, score in compatible_components)
        best_components = [
            component_index
            for component_index, score in compatible_components
            if score == best_score
        ]
        if len(best_components) != 1:
            components.append([index])
            continue
        components[best_components[0]].append(index)

    for component in components:
        if len(component) < 2:
            continue
        members = tuple(rows[index] for index in component)
        has_missing_layout = any(
            _layout_signature(getattr(item, "layout", "")) is None
            for item in members
        )
        groups.append(
            ListingDuplicateGroup(
                members=members,
                reason=(
                    "小区全称/简称一致，面积、单价、总价精确一致，户型缺失不参与比较"
                    if has_missing_layout
                    else "小区全称/简称一致，面积、单价、总价、户型全部精确一致"
                ),
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
    """先同平台去重，再跨平台去重。"""
    raw_items = list(items)
    same_platform_items = deduplicate_same_platform(raw_items)
    cross_platform_items, groups = deduplicate_cross_platform(same_platform_items)
    return ListingDeduplicationResult(
        same_platform_items=tuple(same_platform_items),
        items=tuple(cross_platform_items),
        cross_platform_groups=tuple(groups),
        raw_count=len(raw_items),
    )
