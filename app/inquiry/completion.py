# -*- coding: utf-8 -*-
"""原始 RPA 采集与 HTTP 结果之间的完成编排。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from app.algorithm.branch_text import BRANCH_TEXT
from app.inquiry.aggregation import build_inquiry_result, log_inquiry_result
from app.inquiry.models import (
    ConfirmedCommunityContext,
    InquiryCompletionPayload,
    InquiryResult,
)
from app.property_records.ingestion import record_platform_result
from app.rpa.core.models import InquiryRequest, RPACollectionResult
from app.rpa.core.status import PlatformResultStatus, TASK_STATUS_TEXT, TaskStatus


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
    """持有最终聚合，让 RPA 只负责采集。

    主链先做跨平台估价并立即返回；数据沉淀（落库挂牌/成交）转后台执行
    （sqlite 写入放线程池，避免阻塞事件循环与后续任务），失败仅 warning。
    落库是幂等重跑型副作用，弱保证（进程在返回后立刻退出可能丢一次，
    下次重采即补齐）；需要强保证时可在停服前调用 wait_background。
    """

    def __init__(self) -> None:
        self._background_tasks: set[asyncio.Task] = set()

    async def wait_background(self) -> None:
        """等待尚未完成的后台落库任务（停服前调用可保证落库不丢）。"""
        pending = [task for task in self._background_tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _spawn_record(
        self,
        context: ConfirmedCommunityContext,
        collection: RPACollectionResult,
    ) -> None:
        task = asyncio.create_task(self._record_async(context, collection))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _record_async(
        self,
        context: ConfirmedCommunityContext,
        collection: RPACollectionResult,
    ) -> None:
        try:
            await asyncio.to_thread(self._record_collection, context, collection)
        except Exception as exc:  # 落库失败不阻塞询价返回
            log.warning("后台落库失败：%s", exc)

    @staticmethod
    def _record_collection(
        context: ConfirmedCommunityContext,
        collection: RPACollectionResult,
    ) -> None:
        """把本次询价各成功平台结果落库（按小区一批，单平台一次 ingest）。"""
        recorded = 0
        for result in collection.platform_results:
            if getattr(result, "status", None) != PlatformResultStatus.SUCCESS:
                continue
            if not getattr(result, "listing_page_url", None):
                continue
            try:
                report = record_platform_result(
                    community_id=context.community_id,
                    city=context.city,
                    administrative_district=context.administrative_district,
                    source_community_name=context.canonical_name,
                    result=result,
                    listing_page_url=result.listing_page_url,
                    deal_page_url=getattr(result, "deal_page_url", None),
                )
            except Exception as exc:
                log.warning(
                    "询价落库失败：%s(%s) 平台=%s：%s",
                    context.canonical_name,
                    context.community_id,
                    getattr(result, "name", ""),
                    exc,
                )
                continue
            recorded += 1
            log.info(
                "询价落库：%s(%s) 平台=%s 挂牌 %d 条(带URL跳过 %d) 成交 %d 条",
                context.canonical_name,
                context.community_id,
                getattr(result, "name", ""),
                len(report.listings),
                len(report.skipped_listings),
                len(report.deals),
            )
        if recorded:
            log.info("询价任务落库完成：%s(%s) 平台 %d 个", context.canonical_name, context.community_id, recorded)

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
            if context is not None:
                # 估价已完成，落库转后台执行（幂等重跑型副作用，弱保证）
                self._spawn_record(context, collection)
            return InquiryCompletionPayload(task_result, callback_payload)

        return complete
