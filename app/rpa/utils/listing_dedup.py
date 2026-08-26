# -*- coding: utf-8 -*-
"""保守的同平台与跨平台房源去重工具。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Generic, Iterable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ListingDuplicateGroup(Generic[T]):
    """高置信度的跨平台重复房源组。"""

    members: tuple[T, ...]
    reason: str

    @property
    def representative(self) -> T:
        return self.members[0]


@dataclass(frozen=True)
class ListingDeduplicationResult(Generic[T]):
    """两阶段房源去重流程的结果。"""

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
    """规范化不同网站之间无实际影响的标点和空格差异。"""
    text = _text(value).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace()
        and unicodedata.category(character)[0] not in {"P", "S"}
    )


def _normalized_community(value: Any) -> str:
    """标准化小区名，并忽略末尾括号中的行政区后缀。"""
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


def _communities_match_for_strong_title(left: Any, right: Any) -> bool:
    """强标题匹配时允许一方仅多出明确的住宅通用后缀。"""
    left_community = _normalized_community(left)
    right_community = _normalized_community(right)
    if not left_community or not right_community:
        return False
    if left_community == right_community:
        return True
    shorter, longer = sorted((left_community, right_community), key=len)
    return any(longer == f"{shorter}{suffix}" for suffix in _RESIDENTIAL_COMMUNITY_SUFFIXES)


def _communities_share_distinctive_name(left: Any, right: Any) -> bool:
    """判断小区全称与无歧义简称是否指向同一名称。"""
    left_community = _normalized_community(left)
    right_community = _normalized_community(right)
    if not left_community or not right_community:
        return False
    if left_community == right_community:
        return True
    shorter, longer = sorted((left_community, right_community), key=len)
    return len(shorter) >= 3 and shorter in longer


def _layout_signature(value: Any) -> tuple[int, int] | None:
    """从中文或英文户型文本中提取室数和厅数。"""
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
    """仅在完成无实际影响的空格和标点规范化后匹配标题。"""
    left_title = _normalized_text(left)
    right_title = _normalized_text(right)
    if not left_title or not right_title:
        return False
    return left_title == right_title


def listing_dedup_key(item: T) -> tuple[Any, ...] | None:
    """为同平台内确定重复的房源构建键值。"""
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
    """识别缺少房源编号或户型时仍具备强证据的同平台重复记录。"""
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
    """在确定重复时优先保留标题和结构化字段更完整的一条。"""
    return (
        int(_layout_signature(getattr(item, "layout", "")) is not None),
        len(_normalized_text(getattr(item, "title", ""))),
        len(_normalized_community(getattr(item, "community_name", ""))),
    )


def deduplicate_same_platform(items: Iterable[T]) -> list[T]:
    """按稳定编号或完整字段去重，并处理缺字段时的强证据重复。"""
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


def _cross_platform_match_score(
    left: T,
    right: T,
    *,
    area_tolerance: float,
    unit_price_tolerance: float,
) -> tuple[int, float, float, float] | None:
    """返回跨平台核心字段匹配分数；不匹配时返回 None。"""
    left_platform = _normalized_text(getattr(left, "platform", ""))
    right_platform = _normalized_text(getattr(right, "platform", ""))
    if not left_platform or left_platform == right_platform:
        return None

    communities_match = _communities_share_distinctive_name(
        getattr(left, "community_name", ""),
        getattr(right, "community_name", ""),
    )
    if not communities_match:
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


def _cross_platform_match(
    left: T,
    right: T,
    *,
    area_tolerance: float,
    unit_price_tolerance: float,
) -> bool:
    """返回两行数据是否可以安全视为同一套房源。"""
    return _cross_platform_match_score(
        left,
        right,
        area_tolerance=area_tolerance,
        unit_price_tolerance=unit_price_tolerance,
    ) is not None


def deduplicate_cross_platform(
    items: Iterable[T],
    *,
    area_tolerance: float = 0.5,
    unit_price_tolerance: float = 100.0,
) -> tuple[list[T], list[ListingDuplicateGroup[T]]]:
    """仅合并跨平台的、无歧义的重复房源组。"""
    rows = list(items)
    matched_indexes: set[int] = set()
    representative_indexes: set[int] = set()
    groups: list[ListingDuplicateGroup[T]] = []
    components: list[list[int]] = []

    # 每行数据只与房源组的首行比较。
    # 这样可以将代表项作为身份锚点，避免两行数据仅因恰好共享另一条匹配项就被直接判定为相等。
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
            score = _cross_platform_match_score(
                row,
                rows[component[0]],
                area_tolerance=area_tolerance,
                unit_price_tolerance=unit_price_tolerance,
            )
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
        # 多个候选完全同分时仍保留独立房源；只有存在明确的最佳候选才合并。
        if len(best_components) != 1:
            components.append([index])
            continue
        components[best_components[0]].append(index)

    for component in components:
        if len(component) < 2:
            continue
        members = tuple(rows[index] for index in component)
        representative = members[0]
        has_missing_layout = any(
            _layout_signature(getattr(item, "layout", "")) is None
            for item in members
        )
        reason = (
            "\u5c0f\u533a\u5168\u79f0/\u7b80\u79f0\u4e00\u81f4\uff0c\u9762\u79ef\u3001\u5355\u4ef7\u3001\u603b\u4ef7\u7cbe\u786e\u4e00\u81f4\uff0c\u6237\u578b\u7f3a\u5931\u4e0d\u53c2\u4e0e\u6bd4\u8f83"
            if has_missing_layout
            else "\u5c0f\u533a\u5168\u79f0/\u7b80\u79f0\u4e00\u81f4\uff0c\u9762\u79ef\u3001\u5355\u4ef7\u3001\u603b\u4ef7\u3001\u6237\u578b\u5168\u90e8\u7cbe\u786e\u4e00\u81f4"
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
    """先执行同平台去重，再执行保守的跨平台去重。"""
    raw_items = list(items)
    same_platform_items = deduplicate_same_platform(raw_items)
    cross_platform_items, groups = deduplicate_cross_platform(same_platform_items)
    return ListingDeduplicationResult(
        same_platform_items=tuple(same_platform_items),
        items=tuple(cross_platform_items),
        cross_platform_groups=tuple(groups),
        raw_count=len(raw_items),
    )
