# -*- coding: utf-8 -*-
"""顶层 FastAPI 组合入口。"""

from __future__ import annotations

import logging
import math
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.algorithm import (
    get_weighted_median_discount,
    is_weighted_median_discount_default,
    set_weighted_median_discount,
)
from app.inquiry.completion import InquiryCompletionOrchestrator
from app.inquiry.models import InquirySubmissionStatus
from app.inquiry.orchestrator import InquiryOrchestrator
from app.inquiry.task_manager import InquiryTaskManager
from app.rpa.core.status import TaskStatus
from app.rpa.runtime import RPARuntime

log = logging.getLogger(__name__)


class InquiryCreatePayload(BaseModel):
    city: str = Field(..., min_length=1, description="城市名（如 深圳、广州）")
    administrative_district: str = Field(
        ..., min_length=1, alias="administrativeDistrict", description="行政区（如 南山区）"
    )
    community_name: str = Field(..., min_length=1, alias="communityName")
    area: float = Field(..., gt=0, alias="area")
    request_id: Optional[str] = Field(default=None, alias="requestId")

    model_config = {
        "populate_by_name": True,
    }


class WeightedMedianDiscountPayload(BaseModel):
    weighted_median_discount: float = Field(..., gt=0, lt=1, alias="weightedMedianDiscount")

    model_config = {
        "populate_by_name": True,
    }


def create_app(
    *,
    runtime: Optional[RPARuntime] = None,
    manage_runtime: bool = True,
    inquiry_orchestrator: Optional[InquiryOrchestrator] = None,
    inquiry_task_manager: Optional[InquiryTaskManager] = None,
) -> FastAPI:
    completion_orchestrator = InquiryCompletionOrchestrator()
    if runtime is None:
        runtime = RPARuntime()
    if inquiry_orchestrator is None:
        inquiry_task_manager = inquiry_task_manager or InquiryTaskManager(
            runtime,
            completion_orchestrator,
        )
        inquiry_orchestrator = InquiryOrchestrator(
            inquiry_task_manager,
        )
    else:
        inquiry_task_manager = inquiry_task_manager or inquiry_orchestrator.task_manager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        app.state.inquiry_task_manager = inquiry_task_manager
        if manage_runtime:
            await runtime.start()
        await inquiry_task_manager.start()
        try:
            yield
        finally:
            await inquiry_task_manager.stop()
            if manage_runtime:
                await runtime.stop()

    app = FastAPI(title="jeethink-rpa", lifespan=lifespan)

    @app.get("/health/live")
    async def health_live():
        return {"code": "OK", "message": "服务进程运行中", "data": {"status": "存活"}}

    @app.get("/health/ready")
    async def health_ready():
        current_runtime: RPARuntime = app.state.runtime
        snapshot = current_runtime.snapshot()
        task_manager: InquiryTaskManager = app.state.inquiry_task_manager
        snapshot["inquiryRecoveryComplete"] = task_manager.recovery_complete
        if current_runtime.is_ready() and task_manager.recovery_complete:
            return {"code": "OK", "message": "服务已就绪", "data": snapshot}
        return JSONResponse(
            status_code=503,
            content={"code": "SERVICE_NOT_READY", "message": "询价服务尚未就绪", "data": snapshot},
        )

    @app.get("/admin/status")
    async def admin_status():
        current_runtime: RPARuntime = app.state.runtime
        snapshot = current_runtime.snapshot()
        snapshot["inquiryRecoveryComplete"] = app.state.inquiry_task_manager.recovery_complete
        return {"code": "OK", "message": "查询成功", "data": snapshot}

    @app.post("/admin/platforms/{code}/confirm-ready")
    async def confirm_ready(code: str):
        current_runtime: RPARuntime = app.state.runtime
        try:
            data = await current_runtime.confirm_platform_ready(code)
            return {"code": "OK", "message": "平台状态已更新", "data": data}
        except KeyError:
            raise HTTPException(status_code=404, detail="未找到对应平台")
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @app.post("/inquiries", status_code=202)
    async def create_inquiry(payload: InquiryCreatePayload):
        current_runtime: RPARuntime = app.state.runtime
        try:
            submission = await inquiry_orchestrator.submit(
                city=payload.city,
                administrative_district=payload.administrative_district,
                community_name=payload.community_name,
                area=payload.area,
                request_id=payload.request_id,
            )
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "SERVICE_NOT_READY",
                    "message": "询价服务尚未就绪",
                    "data": current_runtime.snapshot(),
                },
            )
        if submission.status == InquirySubmissionStatus.COMMUNITY_NOT_FOUND:
            return JSONResponse(
                status_code=200,
                content={
                    "code": "COMMUNITY_NOT_FOUND",
                    "message": "未找到小区",
                    "data": {},
                },
            )
        if submission.status == InquirySubmissionStatus.COMMUNITY_TYPE_NOT_SUPPORTED:
            return JSONResponse(
                status_code=200,
                content={
                    "code": "COMMUNITY_TYPE_NOT_SUPPORTED",
                    "message": "小区类型不支持询价",
                    "data": {
                        "candidates": [
                            {
                                "communityGroupId": candidate.community_group_id,
                                "communityId": candidate.community_id,
                                "canonicalName": candidate.canonical_name,
                                "phase": candidate.phase,
                                "aliases": list(candidate.aliases),
                                "city": candidate.city,
                                "administrativeDistrict": candidate.administrative_district,
                            }
                            for candidate in submission.candidates
                        ]
                    },
                },
            )
        if submission.status == InquirySubmissionStatus.COMMUNITY_PHASE_REQUIRED:
            return JSONResponse(
                status_code=200,
                content={
                    "code": "COMMUNITY_PHASE_REQUIRED",
                    "message": "小区期数未明确",
                    "data": {
                        "candidates": [
                            {
                                "communityGroupId": candidate.community_group_id,
                                "communityId": candidate.community_id,
                                "canonicalName": candidate.canonical_name,
                                "phase": candidate.phase,
                                "aliases": list(candidate.aliases),
                                "city": candidate.city,
                                "administrativeDistrict": candidate.administrative_district,
                            }
                            for candidate in submission.candidates
                        ]
                    },
                },
            )
        task = submission.task
        if task is None:
            raise RuntimeError("询价编排未返回任务")
        return {
            "code": "ACCEPTED",
            "message": "询价任务已受理",
            "data": {
                "taskId": task["taskId"],
                "status": task["status"],
                "statusCode": task["statusCode"],
            },
        }

    @app.get("/inquiries/{task_id}")
    async def get_inquiry(task_id: str):
        current_runtime: RPARuntime = app.state.runtime
        # 限流：同一 taskId 两次查询最小间隔 GET_INQUIRY_MIN_INTERVAL 秒
        allowed, wait = current_runtime.check_get_allowed(task_id)
        if not allowed:
            retry_after = max(1, math.ceil(wait))
            log.warning(
                "[API限流] GET /inquiries/%s 查询过于频繁，retry_after=%ss；"
                "该 429 来自客户端轮询，不是平台采集接口",
                task_id,
                retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "code": "TOO_MANY_REQUESTS",
                    "message": f"查询过于频繁，请在 {retry_after} 秒后重试",
                    "data": {"taskId": task_id, "retryAfter": retry_after},
                },
                headers={"Retry-After": str(retry_after)},
            )
        task = current_runtime.get_task(task_id)
        if task is None:
            # 不存在的任务不计入限流
            raise HTTPException(status_code=404, detail="未找到对应任务")
        current_runtime.register_get(task_id)
        if task["statusCode"] == TaskStatus.COMPLETED and task["result"] is not None:
            result = task["result"]
            # 补状态字段：全平台 NO_DATA 时三个价格都是 None，
            # 若只返回 data 客户端无法判断"已完成但无数据"会死等。
            data = {
                **result["data"],                   # quoteAvg/dealAvg/finalPrice（可能全 None）
                "success": result["success"],       # 无数据时为 False
                "statusCode": task["statusCode"],   # COMPLETED
                "branchCode": result["branchCode"], # NO_DATA / WEIGHTED_MEDIAN / ...
                "branch": result["branch"],
            }
            if result.get("note"):                  # 各平台 NO_DATA 原因汇总
                data["note"] = result["note"]
        else:
            data = {
                "taskId": task["taskId"],
                "status": task["status"],
                "statusCode": task["statusCode"],
            }
        return {"code": "OK", "message": "查询成功", "data": data}

    @app.get("/admin/algorithm/weighted-median-discount")
    async def get_weighted_median_discount():
        return {
            "code": "OK",
            "message": "查询成功",
            "data": {
                "weightedMedianDiscount": get_weighted_median_discount(),
                "isDefault": is_weighted_median_discount_default(),
            },
        }

    @app.put("/admin/algorithm/weighted-median-discount")
    async def update_weighted_median_discount(payload: WeightedMedianDiscountPayload):
        try:
            new_value = set_weighted_median_discount(payload.weighted_median_discount)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "code": "OK",
            "message": "参数已更新",
            "data": {"weightedMedianDiscount": new_value},
        }

    return app


app = create_app()
