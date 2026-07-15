# -*- coding: utf-8 -*-
"""FastAPI 入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core import config
from app.core.models import InquiryRequest
from app.runtime import RPARuntime


class InquiryCreatePayload(BaseModel):
    community_name: str = Field(..., min_length=1, alias="communityName")
    area_min: float = Field(..., gt=0, alias="areaMin")
    area_max: float = Field(..., gt=0, alias="areaMax")
    city: str = "深圳"
    request_id: Optional[str] = Field(default=None, alias="requestId")

    model_config = {
        "populate_by_name": True,
    }


class NoDealDiscountPayload(BaseModel):
    no_deal_discount: float = Field(..., gt=0, lt=1, alias="noDealDiscount")

    model_config = {
        "populate_by_name": True,
    }


def create_app(*, runtime: Optional[RPARuntime] = None, manage_runtime: bool = True) -> FastAPI:
    runtime = runtime or RPARuntime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        if manage_runtime:
            await runtime.start()
        try:
            yield
        finally:
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
        if current_runtime.is_ready():
            return {"code": "OK", "message": "服务已就绪", "data": snapshot}
        return JSONResponse(
            status_code=503,
            content={"code": "SERVICE_NOT_READY", "message": "RPA 服务尚未就绪", "data": snapshot},
        )

    @app.get("/admin/status")
    async def admin_status():
        current_runtime: RPARuntime = app.state.runtime
        return {"code": "OK", "message": "查询成功", "data": current_runtime.snapshot()}

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
        request = InquiryRequest(
            community_name=payload.community_name,
            area_min=payload.area_min,
            area_max=payload.area_max,
            city=payload.city,
            request_id=payload.request_id,
        )
        try:
            task = await current_runtime.enqueue_inquiry(request)
            return {
                "code": "ACCEPTED",
                "message": "询价任务已受理",
                "data": {
                    "taskId": task["taskId"],
                    "status": task["status"],
                    "statusCode": task["statusCode"],
                },
            }
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "SERVICE_NOT_READY",
                    "message": "RPA 服务尚未就绪",
                    "data": current_runtime.snapshot(),
                },
            )

    @app.get("/inquiries/{task_id}")
    async def get_inquiry(task_id: str):
        current_runtime: RPARuntime = app.state.runtime
        task = current_runtime.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="未找到对应任务")
        if task["statusCode"] == "COMPLETED" and task["result"] is not None:
            data = task["result"]["data"]
        else:
            data = {
                "taskId": task["taskId"],
                "status": task["status"],
                "statusCode": task["statusCode"],
            }
        return {"code": "OK", "message": "查询成功", "data": data}

    @app.get("/admin/algorithm/no-deal-discount")
    async def get_no_deal_discount():
        return {
            "code": "OK",
            "message": "查询成功",
            "data": {
                "noDealDiscount": config.get_no_deal_discount(),
                "isDefault": config.is_no_deal_discount_default(),
            },
        }

    @app.put("/admin/algorithm/no-deal-discount")
    async def update_no_deal_discount(payload: NoDealDiscountPayload):
        try:
            new_value = config.set_no_deal_discount(payload.no_deal_discount)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "code": "OK",
            "message": "参数已更新",
            "data": {"noDealDiscount": new_value},
        }

    return app


app = create_app()
