# -*- coding: utf-8 -*-
"""RPA 服务编排。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, Optional

from app.core import config
from app.core.algorithm import (
    AlgorithmInput,
    WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION,
    evaluate_algorithm,
)
from app.core.models import (
    InquiryRequest,
    InquiryResult,
    PlatformResult,
    PlatformSession,
    PriceCandidate,
)
from app.core.status import PlatformResultStatus
from app.platforms.base import (
    LISTING_AREA_TOLERANCE,
    PlatformAdapter,
    manual_verify_events_snapshot,
    manual_verify_waiting_snapshot,
    reset_manual_verify_events,
)
from app.core.price_utils import format_price, round_price
from app.utils.listing_dedup import deduplicate_listings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AlgorithmListing:
    """应用跨平台通用去重规则所需的房源字段。"""

    platform: str
    house_id: str
    community_name: Optional[str]
    title: Optional[str]
    area: Optional[float]
    layout: Optional[str]
    unit_price: Optional[float]
    total_price: Optional[float]


def _algorithm_quote_price_lists(
    successful_results: list[PlatformResult],
) -> list[list[float]]:
    """完成同平台和跨平台去重后，构建算法价格列表。"""
    listings: list[_AlgorithmListing] = []
    fallback_price_lists: list[list[float]] = []
    for result in successful_results:
        snapshots = [
            snapshot
            for snapshot in result.listing_snapshots
            if snapshot.unit_price is not None and snapshot.unit_price > 0
        ]
        if not snapshots:
            fallback_price_lists.append(result.quote_prices)
            continue
        listings.extend(
            _AlgorithmListing(
                platform=result.name,
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
    deduplicated_prices = [
        listing.unit_price
        for listing in deduplication.items
        if listing.unit_price is not None and listing.unit_price > 0
    ]
    price_lists = [deduplicated_prices]
    price_lists.extend(fallback_price_lists)
    return price_lists


def _algorithm_deal_price_lists(
    successful_results: list[PlatformResult],
) -> list[list[float]]:
    """构建用于最终等权平均的真实目标面积成交价。

    安居客和乐有家虽然没有成交记录，但为了兼容结果结构会填充
    ``deal_prices``；这类顶替值不能触发新的平均步骤。
    """
    return [
        [price for price in result.deal_prices if price is not None and price > 0]
        for result in successful_results
        if result.deal_source not in {"挂牌均价顶替", "小区均价顶替"}
        and any(price is not None and price > 0 for price in result.deal_prices)
    ]


def _reference_prices(result: PlatformResult) -> list[float]:
    """返回严格面积范围之外新增房源的价格值。"""
    if (
        result.listing_snapshots
        and result.reference_area_min is not None
        and result.reference_area_max is not None
    ):
        request_area = (result.reference_area_min + result.reference_area_max) / 2
        return [
            snapshot.unit_price
            for snapshot in result.listing_snapshots
            if snapshot.area is not None
            and abs(snapshot.area - request_area) > LISTING_AREA_TOLERANCE
            and snapshot.unit_price is not None
            and snapshot.unit_price > 0
        ]
    # 兼容测试中手动构造的 PlatformResult，以及未持久化房源快照的旧调用方。
    return [price for price in result.quote_prices if price is not None and price > 0]


def _reference_contributors(
    platform_results: list[PlatformResult],
    selected_quote: Optional[float],
) -> list[PlatformResult]:
    """返回对选定价格峰值有贡献的弱参考平台。"""
    if selected_quote is None or selected_quote <= 0:
        return []
    contributors = []
    for result in platform_results:
        if not result.reference_code:
            continue
        if (
            result.reference_area_min is None
            or result.reference_area_max is None
            or result.reference_listing_count is None
        ):
            continue
        if any(
            price is not None
            and price > 0
            and abs(price - selected_quote) / selected_quote
            <= WEIGHTED_MEDIAN_MAX_RELATIVE_DEVIATION
            for price in _reference_prices(result)
        ):
            contributors.append(result)
    return contributors


def build_inquiry_result(
    platform_results: list[PlatformResult],
) -> InquiryResult:
    """使用唯一的加权落点中位数算法计算最终价。"""
    successful_results = [
        r for r in platform_results if r.status == PlatformResultStatus.SUCCESS
    ]
    evaluation = evaluate_algorithm(
        inputs=AlgorithmInput(
            quote_price_lists=_algorithm_quote_price_lists(successful_results),
            weighted_median_discount=config.get_weighted_median_discount(),
            deal_price_lists=_algorithm_deal_price_lists(successful_results),
        ),
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
    references = _reference_contributors(successful_results, evaluation.quote_avg)
    reference = {}
    reference_tolerances = [
        result.reference_area_tolerance
        for result in references
        if result.reference_area_tolerance is not None
    ]
    reference_mins = [
        result.reference_area_min
        for result in references
        if result.reference_area_min is not None
    ]
    reference_maxes = [
        result.reference_area_max
        for result in references
        if result.reference_area_max is not None
    ]
    if references and reference_mins and reference_maxes:
        reference = {
            "reference_code": "WEAK_AREA_REFERENCE",
            "reference_area_tolerance": max(reference_tolerances or [0.0]),
            "reference_area_min": min(reference_mins),
            "reference_area_max": max(reference_maxes),
            "reference_listing_count": sum(
                result.reference_listing_count or 0 for result in references
            ),
        }

    if evaluation.decision.final_price is None:
        # 全部平台都不支持该城市时，返回简洁提示
        all_city_unsupported = (
            len(platform_results) > 0
            and all(
                r.status == PlatformResultStatus.NO_DATA and "不支持城市" in (r.reason or "")
                for r in platform_results
            )
        )
        if all_city_unsupported:
            note = "不支持该城市"
        elif platform_results and all(
            r.status == PlatformResultStatus.NO_MATCHING_AREA for r in platform_results
        ):
            note = "; ".join(
                f"{r.name}: {r.reason}" for r in platform_results if r.reason
            ) or "所有平台均无匹配面积房源"
            return InquiryResult(
                success=False,
                branch="NO_MATCHING_AREA",
                note=note,
                platform_results=platform_results,
            )
        else:
            reasons = [f"{r.name}: {r.reason}" for r in platform_results if r.reason]
            note = "; ".join(reasons) if reasons else "所有平台均无数据"
        return InquiryResult(success=False, branch="NO_DATA", note=note, platform_results=platform_results)

    return InquiryResult(
        success=evaluation.decision.final_price is not None,
        final_price=round_price(evaluation.decision.final_price),
        branch=evaluation.decision.branch,
        note=(
            "挂牌价与目标面积成交价等权平均"
            if evaluation.decision.branch == "WEIGHTED_MEDIAN_COMBINED"
            else (
                "\u68c0\u6d4b\u5230\u591a\u4e2a\u9ad8\u9891\u4ef7\u683c\u843d\u70b9\uff0c\u53d6\u6700\u4f4e\u4ef7\u683c\u5cf0\u4e2d\u4f4d\u6570\uff0c\u4e0d\u6253\u6298"
                if evaluation.decision.branch == "WEIGHTED_MEDIAN_MULTI"
                else None
            )
        ),
        quote_avg=round_price(evaluation.quote_avg),
        deal_avg=round_price(evaluation.deal_avg),
        platform=None,
        platform_results=platform_results,
        candidates=rounded_candidates if len(rounded_candidates) > 1 else [],
        **reference,
    )


class RPAInquiryService:
    """管理常驻浏览器、平台标签页和单次询价流程。"""

    def __init__(self, browsers: dict, adapters: Iterable[PlatformAdapter]):
        self.browsers = browsers            # {code: browser}
        self.adapters = list(adapters)
        self.sessions: dict[str, PlatformSession] = {}

    async def start(self):
        for adapter in self.adapters:
            browser = self.browsers[adapter.code]
            # 每个平台有独立浏览器，只需导航空白标签页即可
            session = await adapter.open_session(browser, new_tab=False)
            self.sessions[adapter.code] = session
            log.info("platform ready: %s -> %s", adapter.name, session.start_url)
        return self.sessions

    def list_sessions(self) -> list[PlatformSession]:
        return list(self.sessions.values())

    async def run_inquiry(
        self,
        request: InquiryRequest,
        platform_codes: Optional[list[str]] = None,
        before_aggregate: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> InquiryResult:
        reset_manual_verify_events()
        log.info(
            "查询城市: %s, 小区: %s, 面积: %.1f㎡",
            request.city,
            request.community_name,
            request.area,
        )
        if platform_codes is None:
            adapters = self.adapters
        else:
            codes = set(platform_codes)
            adapters = [adapter for adapter in self.adapters if adapter.code in codes]

        async def _collect_one(adapter):
            session = self.sessions[adapter.code]
            browser = self.browsers[adapter.code]
            try:
                await session.page.activate()
                return await adapter.collect(browser, session, request)
            except Exception as exc:
                log.exception("%s 采集异常", adapter.name)
                return PlatformResult(
                    name=adapter.name,
                    status=PlatformResultStatus.ERROR,
                    reason=str(exc),
                    request_id=request.request_id,
                )

        platform_results: list[PlatformResult] = await asyncio.gather(
            *[_collect_one(a) for a in adapters], return_exceptions=True
        )

        waiting = manual_verify_waiting_snapshot()
        if waiting:
            # 通常 gather 要等平台等待任务结束后才能返回。
            # 显式保留此保护，避免未来的后台风控监控器在人工确认仍未完成时执行汇总。
            log.warning("汇总前仍有平台等待人工风控处理: %s", ", ".join(waiting))
            while waiting:
                await asyncio.sleep(0.2)
                waiting = manual_verify_waiting_snapshot()

        if before_aggregate is not None:
            await before_aggregate()

        log.info("所有平台采集协程已完成，人工风控等待已清空，开始汇总")

        risk_events = manual_verify_events_snapshot()
        if risk_events:
            summary = "; ".join(f"{context}={status}" for context, status in risk_events)
            log.warning(
                "[本次询价风控汇总] request_id=%s: %s",
                request.request_id,
                summary,
            )

        inquiry_result = build_inquiry_result(platform_results)
        self._log_inquiry_result(inquiry_result)
        return inquiry_result

    def _log_inquiry_result(self, inquiry_result: InquiryResult):
        if inquiry_result.reference_code:
            log.info(
                "finalWeakReference: referenceCode=%s referenceAreaTolerance=%.2f "
                "referenceAreaMin=%.2f referenceAreaMax=%.2f referenceListingCount=%d",
                inquiry_result.reference_code,
                inquiry_result.reference_area_tolerance or 0.0,
                inquiry_result.reference_area_min or 0.0,
                inquiry_result.reference_area_max or 0.0,
                inquiry_result.reference_listing_count or 0,
            )
        log.info("finalBranch: branchCode=%s", inquiry_result.branch)

        for platform_result in inquiry_result.platform_results:
            # 在售房源
            if platform_result.listing_snapshots:
                for item in platform_result.listing_snapshots:
                    log.info(
                        "%s: {小区名称: %s, 标题: %s, 面积: %s平米, 几房几厅: %s, 售价: %s元/平, 总价: %s万, 房源编号: %s}",
                        platform_result.name,
                        item.community_name or "",
                        item.title or "",
                        item.area if item.area is not None else "",
                        item.layout or "",
                        item.unit_price if item.unit_price is not None else "",
                        item.total_price if item.total_price is not None else "",
                        item.house_id or "",
                    )
            else:
                log.info(
                    "%s: {状态: %s, 原因: %s}",
                    platform_result.name,
                    platform_result.status,
                    platform_result.reason or "",
                )

            if platform_result.reference_code:
                log.info(
                    "%s弱参考: referenceCode=%s referenceAreaTolerance=%.2f "
                    "referenceAreaMin=%.2f referenceAreaMax=%.2f "
                    "referenceListingCount=%d",
                    platform_result.name,
                    platform_result.reference_code,
                    platform_result.reference_area_tolerance or 0.0,
                    platform_result.reference_area_min or 0.0,
                    platform_result.reference_area_max or 0.0,
                    platform_result.reference_listing_count or 0,
                )

            # 成交记录
            deal_records = platform_result.deal_records
            if deal_records:
                for r in deal_records:
                    log.info(
                        "%s成交: {面积: %s㎡, 日期: %s, 总价: %s万, 单价: %s元/平}",
                        platform_result.name,
                        r.get("area", ""),
                        r.get("date", ""),
                        r.get("total_price", ""),
                        r.get("price", ""),
                    )
            elif platform_result.deal_source:
                log.info(
                    "%s成交: 无（%s %s元/㎡）",
                    platform_result.name,
                    platform_result.deal_source,
                    platform_result.deal_prices[0] if platform_result.deal_prices else "—",
                )
            elif platform_result.deal_prices:
                log.info(
                    "%s成交: %s（共%d条）",
                    platform_result.name,
                    platform_result.deal_prices,
                    len(platform_result.deal_prices),
                )
            else:
                log.info("%s成交: 未采集到", platform_result.name)

        log.info("在售均价(单位:元/平): %s", format_price(inquiry_result.quote_avg))
        log.info("成交均价(单位:元/平): %s", format_price(inquiry_result.deal_avg))
        log.info("最终取值(单位:元/平): %s", format_price(inquiry_result.final_price))
