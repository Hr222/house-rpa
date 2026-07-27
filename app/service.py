# -*- coding: utf-8 -*-
"""RPA 服务编排。"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Iterable, Optional

from app.core import config
from app.core.algorithm import AlgorithmInput, evaluate_algorithm
from app.core.models import (
    InquiryRequest,
    InquiryResult,
    PlatformResult,
    PlatformSession,
    PriceCandidate,
)
from app.platforms.base import (
    PlatformAdapter,
    manual_verify_events_snapshot,
    manual_verify_waiting_snapshot,
    reset_manual_verify_events,
)
from app.core.price_utils import format_price, round_price

log = logging.getLogger(__name__)


def build_inquiry_result(
    platform_results: list[PlatformResult],
    algorithm_mode: str = "default",
) -> InquiryResult:
    """所有平台累加平均后计算最终价。无数据(空列表)的平台不参与。

    algorithm_mode:
        "default"    — 现有算法（成交+在售，via decide()）
        "quote_only" — 纯在售算法（只用在售均价打折，via decide_quote_only()）
    """
    successful_results = [r for r in platform_results if r.status == "SUCCESS"]
    evaluation = evaluate_algorithm(
        algorithm_mode=algorithm_mode,
        inputs=AlgorithmInput(
            quote_price_lists=[r.quote_prices for r in successful_results],
            community_avg_prices=[r.community_avg_price for r in successful_results],
            deal_price_lists=[r.deal_prices for r in successful_results],
            diff_threshold=config.DEAL_DIFF_THRESHOLD,
            no_deal_discount=config.get_no_deal_discount(),
            quote_only_discount=config.get_quote_only_discount(),
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

    if evaluation.quote_avg is None:
        # 全部平台都不支持该城市时，返回简洁提示
        all_city_unsupported = (
            len(platform_results) > 0
            and all(
                r.status == "NO_DATA" and "不支持城市" in (r.reason or "")
                for r in platform_results
            )
        )
        if all_city_unsupported:
            note = "不支持该城市"
        elif platform_results and all(
            r.status == "NO_MATCHING_AREA" for r in platform_results
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
            "\u68c0\u6d4b\u5230\u591a\u4e2a\u9ad8\u9891\u4ef7\u683c\u843d\u70b9\uff0c\u53d6\u6700\u4f4e\u4ef7\u683c\u5cf0\u4e2d\u4f4d\u6570\uff0c\u4e0d\u6253\u6298"
            if evaluation.decision.branch == "WEIGHTED_MEDIAN_MULTI"
            else None
        ),
        quote_avg=round_price(evaluation.quote_avg),
        deal_avg=round_price(evaluation.deal_avg),
        platform=None,
        platform_results=platform_results,
        candidates=rounded_candidates if len(rounded_candidates) > 1 else [],
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
                    status="ERROR",
                    reason=str(exc),
                    request_id=request.request_id,
                )

        platform_results: list[PlatformResult] = await asyncio.gather(
            *[_collect_one(a) for a in adapters], return_exceptions=True
        )

        waiting = manual_verify_waiting_snapshot()
        if waiting:
            # Normally gather cannot return until the platform waiters finish.
            # Keep this guard explicit so a future background risk watcher cannot
            # let aggregation run while manual verification is still pending.
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

        inquiry_result = build_inquiry_result(platform_results, request.algorithm_mode)
        self._log_inquiry_result(inquiry_result)
        return inquiry_result

    def _log_inquiry_result(self, inquiry_result: InquiryResult):
        for platform_result in inquiry_result.platform_results:
            # 在售房源
            if platform_result.listing_snapshots:
                for item in platform_result.listing_snapshots:
                    log.info(
                        "%s: {小区名称: %s, 标题: %s, 面积: %s平米, 几房几厅: %s, 售价: %s元/平, 总价: %s万}",
                        platform_result.name,
                        item.community_name or "",
                        item.title or "",
                        item.area if item.area is not None else "",
                        item.layout or "",
                        item.unit_price if item.unit_price is not None else "",
                        item.total_price if item.total_price is not None else "",
                    )
            else:
                log.info(
                    "%s: {状态: %s, 原因: %s}",
                    platform_result.name,
                    platform_result.status,
                    platform_result.reason or "",
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
