# -*- coding: utf-8 -*-
"""把原始 RPA 采集数据转成最终询价结果。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.algorithm.area_rules import LISTING_AREA_TOLERANCE
from app.algorithm.config import get_weighted_median_discount
from app.algorithm.listing_dedup import deduplicate_listings
from app.algorithm.models import AlgorithmInput, PriceCandidate
from app.algorithm.selection import ListingSelection, listing_area_bounds, select_listings_for_estimation
from app.algorithm.weighted_median import (
    WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
    evaluate_algorithm,
)
from app.inquiry.models import InquiryResult
from app.rpa.core.models import ListingSnapshot, PlatformResult
from app.rpa.core.price_utils import round_price
from app.rpa.core.status import PlatformResultStatus


log = logging.getLogger(__name__)
WEAK_AREA_REFERENCE = "WEAK_AREA_REFERENCE"


@dataclass(frozen=True)
class _AlgorithmListing:
    """跨平台去重共用规则所需的房源字段。"""

    platform: str
    house_id: str
    community_name: Optional[str]
    title: Optional[str]
    area: Optional[float]
    layout: Optional[str]
    unit_price: Optional[float]
    total_price: Optional[float]


@dataclass(frozen=True)
class _SelectedPlatformData:
    result: PlatformResult
    selection: ListingSelection[ListingSnapshot]


def _select_platform_data(
    successful_results: list[PlatformResult],
    request_area: Optional[float],
) -> list[_SelectedPlatformData]:
    if request_area is None:
        return [
            _SelectedPlatformData(
                result=result,
                selection=ListingSelection(
                    snapshots=tuple(result.listing_snapshots),
                    strict_snapshots=tuple(result.listing_snapshots),
                    quote_prices=tuple(
                        float(snapshot.unit_price)
                        for snapshot in result.listing_snapshots
                        if snapshot.unit_price is not None and snapshot.unit_price > 0
                    ),
                    applied_tolerance=0.0,
                    reference_listing_count=0,
                    uses_weak_reference=False,
                ),
            )
            for result in successful_results
        ]
    return [
        _SelectedPlatformData(
            result=result,
            selection=select_listings_for_estimation(
                result.listing_snapshots,
                request_area,
                strict_tolerance=LISTING_AREA_TOLERANCE,
            ),
        )
        for result in successful_results
    ]


def _algorithm_quote_price_lists(
    selected_results: list[_SelectedPlatformData],
) -> list[list[float]]:
    listings: list[_AlgorithmListing] = []
    fallback_price_lists: list[list[float]] = []
    for selected in selected_results:
        snapshots = [
            snapshot
            for snapshot in selected.selection.snapshots
            if snapshot.unit_price is not None and snapshot.unit_price > 0
        ]
        if not snapshots:
            # 只有未带快照的历史调用方才能使用价格列表镜像。
            # 真实原始结果中面积不匹配的数据不得绕过筛选。
            if not selected.result.listing_snapshots:
                fallback_price_lists.append(selected.result.quote_prices)
            continue
        listings.extend(
            _AlgorithmListing(
                platform=selected.result.name,
                house_id=snapshot.house_id or "",
                community_name=snapshot.community_name,
                title=snapshot.title,
                area=snapshot.area,
                layout=snapshot.layout,
                unit_price=snapshot.unit_price,
                total_price=snapshot.total_price,
            )
            for snapshot in snapshots
        )

    if not listings:
        return fallback_price_lists
    deduplication = deduplicate_listings(listings)
    return [
        [
            listing.unit_price
            for listing in deduplication.items
            if listing.unit_price is not None and listing.unit_price > 0
        ],
        *fallback_price_lists,
    ]


def _algorithm_deal_price_lists(
    successful_results: list[PlatformResult],
    request_area: Optional[float],
) -> list[list[float]]:
    """从各平台结果收集成交单价列表（算法层接管）。

    优先用 RPA 回传的原始 deal_records 经算法层口径（screen_deal_records）
    按请求面积收敛；老链路/接口平台仍只带 deal_prices（已平台侧筛好）时
    回退该字段。均价顶替来源不计入真实成交。
    """
    from app.algorithm.deal_screening import screen_deal_records

    lists: list[list[float]] = []
    for result in successful_results:
        if result.deal_source in {"挂牌均价顶替", "小区均价顶替", "无"}:
            continue
        records = result.deal_records or []
        if records and request_area is not None:
            prices = screen_deal_records(records, request_area, platform=result.name)
        else:
            prices = [float(price) for price in result.deal_prices
                      if price is not None and price > 0]
        if any(price is not None and price > 0 for price in prices):
            lists.append(prices)
    return lists


def _luxury_data_is_sparse(selected_results: list[_SelectedPlatformData]) -> bool:
    strict_listings: list[_AlgorithmListing] = []
    strict_platforms: set[str] = set()
    has_weak_reference = False
    for selected in selected_results:
        if selected.selection.uses_weak_reference:
            has_weak_reference = True
        for snapshot in selected.selection.strict_snapshots:
            if snapshot.unit_price is None or snapshot.unit_price <= 0:
                continue
            strict_platforms.add(selected.result.name)
            strict_listings.append(
                _AlgorithmListing(
                    platform=selected.result.name,
                    house_id=snapshot.house_id or "",
                    community_name=snapshot.community_name,
                    title=snapshot.title,
                    area=snapshot.area,
                    layout=snapshot.layout,
                    unit_price=snapshot.unit_price,
                    total_price=snapshot.total_price,
                )
            )

    if not strict_listings:
        return True
    unique_count = len(deduplicate_listings(strict_listings).items)
    return (
        unique_count <= 3
        or len(strict_platforms) <= 1
        or (has_weak_reference and unique_count <= 5)
    )


def _reference_contributors(
    selected_results: list[_SelectedPlatformData],
    selected_quote: Optional[float],
) -> list[_SelectedPlatformData]:
    if selected_quote is None or selected_quote <= 0:
        return []
    return [
        selected
        for selected in selected_results
        if selected.selection.uses_weak_reference
        and any(
            abs(price - selected_quote) / selected_quote
            <= WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION
            for price in selected.selection.quote_prices
        )
    ]


def _reference_result_data(
    selected_results: list[_SelectedPlatformData],
    selected_quote: Optional[float],
    request_area: Optional[float],
) -> dict[str, object]:
    contributors = _reference_contributors(selected_results, selected_quote)
    if not contributors or request_area is None:
        return {}
    tolerances = [item.selection.applied_tolerance for item in contributors]
    return {
        "reference_code": WEAK_AREA_REFERENCE,
        "reference_area_tolerance": max(tolerances),
        "reference_area_min": min(request_area - tolerance for tolerance in tolerances),
        "reference_area_max": max(request_area + tolerance for tolerance in tolerances),
        "reference_listing_count": sum(
            item.selection.reference_listing_count for item in contributors
        ),
    }


def build_inquiry_result(
    platform_results: list[PlatformResult],
    request_area: Optional[float] = None,
) -> InquiryResult:
    """在询价应用层评估原始的成功平台结果。"""
    successful_results = [
        result
        for result in platform_results
        if result.status == PlatformResultStatus.SUCCESS
    ]
    selected_results = _select_platform_data(successful_results, request_area)
    evaluation = evaluate_algorithm(
        AlgorithmInput(
            quote_price_lists=_algorithm_quote_price_lists(selected_results),
            weighted_median_discount=get_weighted_median_discount(),
            deal_price_lists=_algorithm_deal_price_lists(successful_results, request_area),
            area=request_area,
            luxury_data_sparse=_luxury_data_is_sparse(selected_results),
        )
    )

    if evaluation.decision.final_price is None:
        all_city_unsupported = bool(platform_results) and all(
            result.status == PlatformResultStatus.NO_DATA
            and "不支持城市" in (result.reason or "")
            for result in platform_results
        )
        if all_city_unsupported:
            note = "不支持该城市"
        elif platform_results and all(
            result.status == PlatformResultStatus.NO_MATCHING_AREA
            for result in platform_results
        ):
            note = "; ".join(
                f"{result.name}: {result.reason}"
                for result in platform_results
                if result.reason
            ) or "所有平台均无匹配面积房源"
            return InquiryResult(
                success=False,
                branch="NO_MATCHING_AREA",
                note=note,
                platform_results=platform_results,
            )
        elif successful_results and all(
            not selected.selection.snapshots for selected in selected_results
        ):
            return InquiryResult(
                success=False,
                branch="NO_MATCHING_AREA",
                note="所有成功采集的平台均无匹配面积房源",
                platform_results=platform_results,
            )
        else:
            reasons = [
                f"{result.name}: {result.reason}"
                for result in platform_results
                if result.reason
            ]
            note = "; ".join(reasons) if reasons else "所有平台均无数据"
        return InquiryResult(
            success=False,
            branch="NO_DATA",
            note=note,
            platform_results=platform_results,
        )

    rounded_candidates = [
        PriceCandidate(
            quote_price=round_price(candidate.quote_price),
            final_price=round_price(candidate.final_price),
            count=candidate.count,
            frequency=round(candidate.frequency, 6),
            min_price=round_price(candidate.min_price),
            max_price=round_price(candidate.max_price),
        )
        for candidate in evaluation.candidates
    ]
    return InquiryResult(
        success=True,
        final_price=round_price(evaluation.decision.final_price),
        branch=evaluation.decision.branch,
        note=(
            "挂牌价与目标面积成交价等权平均"
            if evaluation.decision.branch == "WEIGHTED_MEDIAN_COMBINED"
            else (
                "检测到多个高频价格落点，取最低价格峰中位数，不打折"
                if evaluation.decision.branch == "WEIGHTED_MEDIAN_MULTI"
                else None
            )
        ),
        quote_avg=round_price(evaluation.quote_avg),
        deal_avg=round_price(evaluation.deal_avg),
        platform_results=platform_results,
        candidates=rounded_candidates if len(rounded_candidates) > 1 else [],
        **_reference_result_data(selected_results, evaluation.quote_avg, request_area),
    )


def log_inquiry_result(result: InquiryResult) -> None:
    """记录最终算法决策，不回写进 RPA。"""
    if result.reference_code:
        log.info(
            "finalWeakReference: referenceCode=%s referenceAreaTolerance=%.2f "
            "referenceAreaMin=%.2f referenceAreaMax=%.2f referenceListingCount=%d",
            result.reference_code,
            result.reference_area_tolerance or 0.0,
            result.reference_area_min or 0.0,
            result.reference_area_max or 0.0,
            result.reference_listing_count or 0,
        )
    log.info("finalBranch: branchCode=%s", result.branch)
