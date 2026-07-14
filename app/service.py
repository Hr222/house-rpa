# -*- coding: utf-8 -*-
"""RPA 服务编排。"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from app.core import config
from app.core.algorithm import decide, mean
from app.core.models import InquiryRequest, InquiryResult, PlatformResult, PlatformSession
from app.platforms.base import PlatformAdapter
from app.core.price_utils import format_price, round_price

log = logging.getLogger(__name__)


def build_inquiry_result(platform_results: list[PlatformResult]) -> InquiryResult:
    """当前版本按平台优先级选第一个成功结果出最终价。"""
    selected = next(
        (result for result in platform_results if result.status == "SUCCESS"),
        None,
    )
    if selected is None:
        selected = next((result for result in platform_results if result.status != "ERROR"), None)

    if selected is None:
        return InquiryResult(success=False, branch="FAILED", platform_results=platform_results)

    quote_avg = selected.community_avg_price or mean(selected.quote_prices)
    deal_avg = mean(selected.deal_prices) if selected.deal_prices else None
    decision = decide(
        quote_avg,
        deal_avg,
        config.DEAL_DIFF_THRESHOLD,
        config.NO_DEAL_DISCOUNT,
    )
    return InquiryResult(
        success=decision.final_price is not None,
        final_price=round_price(decision.final_price),
        branch=decision.branch,
        quote_avg=round_price(quote_avg),
        deal_avg=round_price(deal_avg),
        platform=selected,
        platform_results=platform_results,
    )


class RPAInquiryService:
    """管理常驻浏览器、平台标签页和单次询价流程。"""

    def __init__(self, browser, adapters: Iterable[PlatformAdapter]):
        self.browser = browser
        self.adapters = list(adapters)
        self.sessions: dict[str, PlatformSession] = {}

    async def start(self):
        for adapter in self.adapters:
            session = await adapter.open_session(self.browser)
            self.sessions[adapter.code] = session
            log.info("platform ready: %s -> %s", adapter.name, session.start_url)
        return self.sessions

    def list_sessions(self) -> list[PlatformSession]:
        return list(self.sessions.values())

    async def run_inquiry(
        self,
        request: InquiryRequest,
        platform_codes: Optional[list[str]] = None,
    ) -> InquiryResult:
        log.info(
            "查询小区: %s, 筛选面积: %s 至 %s",
            request.community_name,
            request.area_min,
            request.area_max,
        )
        if platform_codes is None:
            adapters = self.adapters
        else:
            codes = set(platform_codes)
            adapters = [adapter for adapter in self.adapters if adapter.code in codes]

        platform_results: list[PlatformResult] = []
        for adapter in adapters:
            session = self.sessions[adapter.code]
            result = await adapter.collect(self.browser, session, request)
            platform_results.append(result)

        inquiry_result = build_inquiry_result(platform_results)
        self._log_inquiry_result(inquiry_result)
        return inquiry_result

    def _log_inquiry_result(self, inquiry_result: InquiryResult):
        for platform_result in inquiry_result.platform_results:
            if platform_result.listing_snapshots:
                for item in platform_result.listing_snapshots:
                    log.info(
                        "%s: {小区名称: %s, 面积: %s平米, 几房几厅: %s, 售价: %s元/平, 总价: %s万}",
                        platform_result.name,
                        item.community_name or "",
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

        log.info("在售均价(单位:元/平): %s", format_price(inquiry_result.quote_avg))
        log.info("成交均价(单位:元/平): %s", format_price(inquiry_result.deal_avg))
        log.info("最终取值(单位:元/平): %s", format_price(inquiry_result.final_price))
