# -*- coding: utf-8 -*-
"""原始 RPA 采集与 HTTP 结果之间的完成编排。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.algorithm.branch_text import BRANCH_TEXT
from app.algorithm.models import NearbyCommunityInput
from app.inquiry.aggregation import (
    build_inquiry_result,
    collect_deal_samples,
    log_inquiry_result,
)
from app.inquiry.models import (
    ConfirmedCommunityContext,
    InquiryCompletionPayload,
    InquiryResult,
)
from app.property_records.ingestion import (
    normalize_source_platform,
    record_platform_result,
)
from app.rpa.core.models import (
    InquiryRequest,
    ListingSnapshot,
    PlatformResult,
    RPACollectionResult,
)
from app.rpa.core.status import PlatformResultStatus, TASK_STATUS_TEXT, TaskStatus


log = logging.getLogger(__name__)

# 可比市场算法（DEFAULT）的周边小区数量上限：与需求"周边 3 个小区"一致，
# find_nearby_communities 实际命中不足时按返回数量装配。
NEARBY_COMMUNITY_LIMIT = 3

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

    def __init__(self, collection_runner=None) -> None:
        self._background_tasks: set[asyncio.Task] = set()
        # 周边小区冷采集入口：async (InquiryRequest) -> RPACollectionResult。
        # 由 api.py 注入 runtime.service.run_inquiry；为 None（测试/离线）
        # 时冷小区跳过采集，仅读库内热数据，不影响主链路。
        self.collection_runner = collection_runner

    async def wait_background(self) -> None:
        """等待尚未完成的后台落库任务（停服前调用可保证落库不丢）。"""
        pending = [task for task in self._background_tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _spawn_record(
        self,
        context: ConfirmedCommunityContext,
        collection: RPACollectionResult,
        hot_platform_codes: tuple[str, ...],
    ) -> None:
        task = asyncio.create_task(
            self._record_async(context, collection, hot_platform_codes)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _record_async(
        self,
        context: ConfirmedCommunityContext,
        collection: RPACollectionResult,
        hot_platform_codes: tuple[str, ...],
    ) -> None:
        try:
            await asyncio.to_thread(
                self._record_collection, context, collection, hot_platform_codes
            )
        except Exception as exc:  # 落库失败不阻塞询价返回
            log.warning("后台落库失败：%s", exc)

    @staticmethod
    def _record_collection(
        context: ConfirmedCommunityContext,
        collection: RPACollectionResult,
        hot_platform_codes: tuple[str, ...] = (),
    ) -> None:
        """把本次询价各成功平台结果落库（按小区一批，单平台一次 ingest）。

        热平台的结果由库内数据合成，重复落库会把 updated_at 刷成当前时间、
        让热度判定失真，因此跳过。
        """
        hot_codes = set(hot_platform_codes)
        recorded = 0
        for result in collection.platform_results:
            if getattr(result, "status", None) != PlatformResultStatus.SUCCESS:
                continue
            if not getattr(result, "listing_page_url", None):
                continue
            try:
                if normalize_source_platform(getattr(result, "name", "")) in hot_codes:
                    continue
            except ValueError:
                pass
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

    @staticmethod
    def _synthesize_platform_results(
        community_id: int,
        platform_codes: tuple[str, ...] | list[str],
        request_id: str | None,
    ) -> list[PlatformResult]:
        """用库内数据为热平台合成 PlatformResult（仅供估价，不重复落库）。

        数据单位换算：listing_records/deal_records 存元，现行契约用"万"。
        任何异常只降级为"该平台无合成结果"，绝不反噬主链。
        """
        from app.property_records.database import DEAL_PLATFORMS, PropertyRecordsDatabase
        from app.rpa.registry import build_default_adapters

        platform_names = {adapter.code: adapter.name for adapter in build_default_adapters()}
        results: list[PlatformResult] = []
        try:
            database = PropertyRecordsDatabase()
        except Exception:
            log.warning("热数据读取失败（库不可用），本次不合成热结果", exc_info=True)
            return results
        for code in platform_codes:
            try:
                page = database.get_community_platform_page(community_id, code)
                if page is None:
                    continue
                snapshots = [
                    ListingSnapshot(
                        house_id=str(row.id),
                        community_name=row.source_community_name,
                        title=row.title,
                        area=row.area_sqm,
                        layout=row.layout,
                        unit_price=row.unit_price_yuan,
                        total_price=(
                            row.total_price_yuan / 10000
                            if row.total_price_yuan is not None
                            else None
                        ),
                        listing_url=row.listing_url,
                    )
                    for row in database.list_listings(
                        community_id, source_platform=code
                    )
                ]
                deal_records: list[dict] = []
                if code in DEAL_PLATFORMS:
                    for row in database.list_deals(community_id, source_platform=code):
                        deal_records.append(
                            {
                                "area": row.area_sqm,
                                "date": row.deal_date,
                                "total_price": (
                                    row.total_price_yuan / 10000
                                    if row.total_price_yuan is not None
                                    else None
                                ),
                                "price": row.unit_price_yuan,
                            }
                        )
                status = (
                    PlatformResultStatus.SUCCESS
                    if (snapshots or deal_records)
                    else PlatformResultStatus.NO_DATA
                )
                results.append(
                    PlatformResult(
                        name=platform_names.get(code, code),
                        status=status,
                        deal_records=deal_records,
                        deal_source="成交记录" if deal_records else "无",
                        reason="热数据无在架记录" if status == PlatformResultStatus.NO_DATA else None,
                        request_id=request_id,
                        listing_page_url=page.listing_page_url,
                        deal_page_url=page.deal_page_url,
                        listing_snapshots=snapshots,
                    )
                )
                log.info(
                    "[热数据] 平台=%s 合成结果：在售 %d 条，成交 %d 条",
                    platform_names.get(code, code),
                    len(snapshots),
                    len(deal_records),
                )
            except Exception:
                log.warning(
                    "热平台[%s]合成失败，该平台按无结果处理", code, exc_info=True
                )
        return results

    # ===== 可比市场算法：周边小区数据装配（case3/4 输入） =====

    @staticmethod
    def _community_is_hot(community_id: int) -> bool:
        """任一平台在热窗口内更新过数据即视为热（整小区直接读库）。

        与主任务热短路（hot_data.select_hot_platforms，要求 ≥3 平台）
        的差别：周边小区是辅助数据，任一平台新鲜即可复用，避免邻区
        冷启动放大任务时长。查询失败一律按冷处理（宁慢不错）。
        """
        from app.inquiry.hot_data import get_hot_data_max_age_hours
        from app.property_records.database import PropertyRecordsDatabase

        try:
            freshness = PropertyRecordsDatabase().get_platform_freshness(community_id)
        except Exception:
            log.warning(
                "[周边小区] 热度查询失败，按冷处理：community_id=%s",
                community_id,
                exc_info=True,
            )
            return False
        window = timedelta(hours=get_hot_data_max_age_hours())
        now = datetime.now(timezone.utc)
        for raw in freshness.values():
            try:
                last_update = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if last_update.tzinfo is None:
                last_update = last_update.replace(tzinfo=timezone.utc)
            if now - last_update <= window:
                return True
        return False

    @staticmethod
    def _read_community_comparables(
        community_id: int,
        request_area: float,
    ) -> tuple[tuple[float, ...], list[list[float]]]:
        """从房源记录库读一个小区的可比数据（热/冷两条路的统一出口）。

        返回 (deal_prices, quote_price_lists)：
        - 成交：库内跨平台去重（list_deals deduplicate=True）后，再经
          prepare_deal_samples 按与目标小区完全相同的口径（面积 ±10% +
          近半年）筛选，保证可比性口径全链路一致；
        - 挂牌：在架行按请求面积 ±1㎡ 严格筛 + 跨平台去重后按平台分组，
          供该小区找自己的报价频率最高价（case4）。
        单位换算：库存元，采集契约总价用"万"（与热数据合成保持一致）。
        """
        from app.algorithm.area_rules import listing_area_bounds
        from app.algorithm.listing_dedup import deduplicate_listings
        from app.algorithm.market_comparison import prepare_deal_samples
        from app.property_records.database import PropertyRecordsDatabase

        database = PropertyRecordsDatabase()
        deal_records = [
            {
                "area": row.area_sqm,
                "date": row.deal_date,
                "total_price": (
                    row.total_price_yuan / 10000
                    if row.total_price_yuan is not None
                    else None
                ),
                "price": row.unit_price_yuan,
            }
            for row in database.list_deals(community_id, deduplicate=True)
        ]
        deal_prices = prepare_deal_samples([("库内", deal_records)], request_area)

        area_min, area_max = listing_area_bounds(request_area)
        dedup_items = []
        for row in database.list_listings(community_id):
            if row.area_sqm is None or not (area_min <= row.area_sqm <= area_max):
                continue
            if row.unit_price_yuan is None or row.unit_price_yuan <= 0:
                continue
            dedup_items.append(
                # 按 listing_dedup 的字段契约适配库内行（平台用 code 区分）。
                SimpleNamespace(
                    platform=row.source_platform,
                    house_id=str(row.id) if row.id is not None else "",
                    community_name=row.source_community_name,
                    title=row.title,
                    area=row.area_sqm,
                    layout=row.layout,
                    unit_price=float(row.unit_price_yuan),
                    total_price=(
                        row.total_price_yuan / 10000
                        if row.total_price_yuan is not None
                        else None
                    ),
                )
            )
        prices_by_platform: dict[str, list[float]] = {}
        for item in deduplicate_listings(dedup_items).items:
            prices_by_platform.setdefault(item.platform, []).append(item.unit_price)
        return deal_prices, list(prices_by_platform.values())

    async def _collect_nearby_community(
        self,
        record,
        request: InquiryRequest,
    ) -> None:
        """对冷小区做一次全量采集（挂牌+成交，入口平台并行）并同步入库。

        周边数据的用途就是"存库 + 读回"（为后续询价沉淀热数据），因此
        这里必须先落库再读库，不能像主链路那样把落库丢到后台。
        """
        from app.inquiry.platform_entries import load_platform_entries

        listing_pages, deal_pages = load_platform_entries(record.community_id)
        if not listing_pages:
            # 入口白名单未初始化：无从采集，读库自然为空（设计行为）。
            log.info(
                "[周边小区] %s(%s) 无平台入口，跳过采集",
                record.name,
                record.community_id,
            )
            return
        nearby_request = InquiryRequest(
            community_name=record.name,
            area=request.area,
            city=record.city,
            administrative_district=record.administrative_district,
            request_id=request.request_id,
            platform_listing_pages=listing_pages,
            platform_deal_pages=deal_pages,
        )
        collection = await self.collection_runner(nearby_request)
        nearby_context = ConfirmedCommunityContext.from_community_record(
            record,
            area=request.area,
            request_id=request.request_id,
        )
        await asyncio.to_thread(
            self._record_collection, nearby_context, collection, ()
        )

    async def _community_comparables(
        self,
        record,
        request: InquiryRequest,
    ) -> tuple[tuple[float, ...], list[list[float]]]:
        """取一个周边小区的可比数据：热小区直接读库，冷小区先采后读。"""
        if not self._community_is_hot(record.community_id):
            if self.collection_runner is None:
                log.info(
                    "[周边小区] %s(%s) 为冷数据且无采集通道，仅读库",
                    record.name,
                    record.community_id,
                )
            else:
                await self._collect_nearby_community(record, request)
        return await asyncio.to_thread(
            self._read_community_comparables, record.community_id, request.area
        )

    async def _assemble_nearby_communities(
        self,
        context: ConfirmedCommunityContext,
        request: InquiryRequest,
        platform_results: list[PlatformResult],
    ) -> tuple:
        """目标小区无成交时装配周边小区数据（可比市场算法 case3/4）。

        流程：判定目标可比成交样本数（与算法输入同源）→ 为 0 时取
        find_nearby_communities 前 3 个邻近小区 → 逐个"热读库 / 冷采集
        入库后读库"。任何一步失败只降级为"该小区不参与"，绝不反噬
        主询价链路；目标有成交（case1/2）时本方法零开销直接返回。
        """
        from app.community_data import find_nearby_communities

        successful = [
            result
            for result in platform_results
            if result.status == PlatformResultStatus.SUCCESS
        ]
        try:
            target_samples = collect_deal_samples(successful, request.area)
        except Exception:
            log.warning("[周边小区] 目标成交样本判定失败，跳过周边装配", exc_info=True)
            return ()
        if target_samples:
            return ()

        try:
            nearby_records = find_nearby_communities(
                context.city,
                context.administrative_district,
                context.canonical_name,
                limit=NEARBY_COMMUNITY_LIMIT,
            )
        except Exception:
            log.warning(
                "[周边小区] 邻近小区查询失败: %s(%s)",
                context.canonical_name,
                context.community_id,
                exc_info=True,
            )
            return ()
        if not nearby_records:
            log.info(
                "[周边小区] %s(%s) 无可用邻近小区，case3/4 退化为仅目标小区",
                context.canonical_name,
                context.community_id,
            )
            return ()

        nearby_inputs = []
        for record in nearby_records[:NEARBY_COMMUNITY_LIMIT]:
            try:
                deal_prices, quote_price_lists = await self._community_comparables(
                    record, request
                )
            except Exception:
                log.warning(
                    "[周边小区] %s(%s) 数据装配失败，该小区不参与",
                    record.name,
                    record.community_id,
                    exc_info=True,
                )
                continue
            nearby_inputs.append(
                NearbyCommunityInput(
                    community_id=record.community_id,
                    canonical_name=record.name,
                    deal_prices=deal_prices,
                    quote_price_lists=quote_price_lists,
                )
            )
        log.info(
            "[周边小区] 装配完成：%d/%d 个小区参与（目标小区无成交样本）",
            len(nearby_inputs),
            len(nearby_records[:NEARBY_COMMUNITY_LIMIT]),
        )
        return tuple(nearby_inputs)

    def handler_for(
        self,
        context: ConfirmedCommunityContext | None,
        hot_platform_codes: tuple[str, ...] = (),
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
            platform_results = list(collection.platform_results)
            if hot_platform_codes:
                hot_results = self._synthesize_platform_results(
                    context.community_id, hot_platform_codes, request.request_id
                )
                log.info(
                    "[热数据] %d 个平台跳过 RPA，用库内数据合成",
                    len(hot_results),
                )
                platform_results = hot_results + platform_results
            # 可比市场算法（DEFAULT）：目标小区无成交时装配周边小区数据。
            # 有成交（case1/2）时内部零开销直接返回，主链路行为不变。
            nearby_communities: tuple = ()
            if context is not None and request.area is not None:
                nearby_communities = await self._assemble_nearby_communities(
                    context, request, platform_results
                )
            result = build_inquiry_result(
                platform_results,
                request_area=request.area,
                nearby_communities=nearby_communities,
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
                self._spawn_record(context, collection, hot_platform_codes)
            return InquiryCompletionPayload(task_result, callback_payload)

        return complete
