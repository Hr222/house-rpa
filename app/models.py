# -*- coding: utf-8 -*-
"""数据模型。与平台无关，所有 adapter 共用。"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class InquiryRequest(BaseModel):
    """后端 → RPA 的请求。"""
    community_name: str = Field(..., description="小区名称")
    area: float = Field(..., gt=0, description="基准面积(㎡)")
    city: str = Field("深圳", description="城市，MVP 固定深圳")


class Listing(BaseModel):
    """单条在售房源（报价数据）。"""
    unit_price: Optional[float] = Field(None, description="报价单价(元/㎡)")
    total_price: Optional[float] = Field(None, description="总价(万)")
    area: Optional[float] = Field(None, description="面积(㎡)")


class DealRecord(BaseModel):
    """单条成交记录。"""
    unit_price: Optional[float] = Field(None, description="成交单价(元/㎡)")
    area: Optional[float] = Field(None, description="面积(㎡)")


class PlatformResult(BaseModel):
    """单平台采集结果。"""
    name: str
    status: Literal["SUCCESS", "NO_DATA", "BLOCKED", "TIMEOUT", "ERROR", "LOGIN_EXPIRED"]
    community_avg_price: Optional[float] = Field(None, description="详情页小区均价(元/㎡)")
    listings: List[Listing] = Field(default_factory=list, description="在售房源")
    deals: List[DealRecord] = Field(default_factory=list, description="成交记录")
    reason: Optional[str] = None


AlgorithmBranch = Literal["TAKE_LOWER", "DEAL_ONLY", "QUOTE_DISCOUNT", "FAILED"]


class InquiryResponse(BaseModel):
    """RPA → 后端的响应。"""
    success: bool
    final_price: Optional[float] = Field(None, description="最终建议单价(元/㎡)")
    algorithm_branch: AlgorithmBranch
    quote_avg: Optional[float] = None
    deal_avg: Optional[float] = None
    platforms: List[PlatformResult] = Field(default_factory=list)
    elapsed_ms: int = 0
