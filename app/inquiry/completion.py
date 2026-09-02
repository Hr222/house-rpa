# -*- coding: utf-8 -*-
"""原始 RPA 采集与 HTTP 结果之间的完成编排。"""

from __future__ import annotations

import logging
from dataclasses import asdict

from app.algorithm.branch_text import BRANCH_TEXT
from app.inquiry.aggregation import build_inquiry_result, log_inquiry_result
from app.inquiry.models import (
    ConfirmedCommunityContext,
    InquiryCompletionPayload,
    InquiryResult,
)
from app.rpa.core.models import InquiryRequest, RPACollectionResult
from app.rpa.core.status import TASK_STATUS_TEXT, TaskStatus


log = logging.getLogger(__name__)

def _camelize(value):
    if isinstance(value, dict):
        return {
            key.split("_")[0]
            + "".join(part[:1].upper() + part[1:] for part in key.split("_")[1:]): _camelize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _reference_payload(result: InquiryResult) -> dict:
    if not result.reference_code:
        return {}
    return {
        "referenceCode": result.reference_code,
        "referenceAreaTolerance": result.reference_area_tolerance,
        "referenceAreaMin": result.reference_area_min,
        "referenceAreaMax": result.reference_area_max,
        "referenceListingCount": result.reference_listing_count,
    }


def _serialize_platform_result(item) -> dict:
    payload = _camelize(asdict(item))
    payload["statusCode"] = item.status
    return payload


def _task_result_payload(result: InquiryResult) -> dict:
    payload = {
        "success": result.success,
        "finalPrice": result.final_price,
        "branchCode": result.branch,
        "branch": BRANCH_TEXT.get(result.branch, result.branch),
        "quoteAvg": result.quote_avg,
        "dealAvg": result.deal_avg,
        "platform_results": [
            _serialize_platform_result(item) for item in result.platform_results
        ],
        "data": {
            "quoteAvg": result.quote_avg,
            "dealAvg": result.deal_avg,
            "finalPrice": result.final_price,
        },
    }
    if result.candidates:
        candidates = [_camelize(asdict(item)) for item in result.candidates]
        payload["candidates"] = candidates
        payload["data"]["candidates"] = candidates
    reference = _reference_payload(result)
    payload.update(reference)
    payload["data"].update(reference)
    if result.note:
        payload["note"] = result.note
    return payload


class InquiryCompletionOrchestrator:
    """持有最终聚合，让 RPA 只负责采集。"""

    def handler_for(
        self,
        context: ConfirmedCommunityContext | None,
    ):
        async def complete(
            request: InquiryRequest,
            collection: RPACollectionResult,
        ) -> InquiryCompletionPayload:
            if context is not None:
                log.info(
                    "编排层开始汇总原始采集结果: community_id=%s request_id=%s",
                    context.community_id,
                    request.request_id or "-",
                )
            result = build_inquiry_result(
                collection.platform_results,
                request_area=request.area,
            )
            log_inquiry_result(result)
            task_result = _task_result_payload(result)
            callback_payload = {
                "statusCode": TaskStatus.COMPLETED,
                "status": TASK_STATUS_TEXT[TaskStatus.COMPLETED],
                "success": result.success,
                "quoteAvg": result.quote_avg,
                "dealAvg": result.deal_avg,
                "finalPrice": result.final_price,
                "branchCode": result.branch,
                "branch": BRANCH_TEXT.get(result.branch, result.branch),
                **_reference_payload(result),
            }
            if result.candidates:
                callback_payload["candidates"] = task_result["candidates"]
            if result.note:
                callback_payload["note"] = result.note
            return InquiryCompletionPayload(task_result, callback_payload)

        return complete
