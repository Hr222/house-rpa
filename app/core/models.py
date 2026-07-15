# -*- coding: utf-8 -*-
"""数据模型。与平台无关，所有 adapter 共用。"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class InquiryRequest:
    """后端 → RPA 的请求。"""
    community_name: str
    area_min: float
    area_max: float
    city: str = "深圳"
    request_id: Optional[str] = None


@dataclass
class DealRecord:
    """成交案例中的单条记录。"""
    area: Optional[float]
    unit_price: float


@dataclass
class ListingSnapshot:
    """在售房源摘要。"""
    house_id: str
    community_name: Optional[str] = None
    area: Optional[float] = None
    layout: Optional[str] = None
    unit_price: Optional[float] = None
    total_price: Optional[float] = None


@dataclass
class PlatformResult:
    """单平台采集结果。"""
    name: str
    status: str  # SUCCESS / NO_DATA / BLOCKED / LOGIN_EXPIRED / WAIT_MANUAL_VERIFY / ERROR
    community_avg_price: Optional[float] = None   # 详情页小区均价(元/㎡) = P_quote
    quote_prices: List[float] = field(default_factory=list)   # 在售单价列表
    deal_prices: List[float] = field(default_factory=list)    # 成交单价列表(筛选后)
    deal_records: List[dict] = field(default_factory=list)     # 成交记录详情 [{area,date,total,price},...]
    reason: Optional[str] = None
    request_id: Optional[str] = None
    detail_url: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    listing_snapshots: List[ListingSnapshot] = field(default_factory=list)
    deal_source: str = ""   # 成交来源说明: "成交记录" / "挂牌均价顶替" / "小区均价顶替" / "无"


@dataclass(slots=True)
class PlatformSession:
    """平台常驻会话。"""
    code: str
    name: str
    start_url: str
    page: object
    ready: bool = False


@dataclass
class InquiryResult:
    """询价最终结果。"""
    success: bool
    final_price: Optional[float] = None    # 最终建议单价(元/㎡)
    branch: str = "FAILED"                  # TAKE_LOWER / DEAL_ONLY / QUOTE_DISCOUNT / FAILED
    quote_avg: Optional[float] = None
    deal_avg: Optional[float] = None
    platform: Optional[PlatformResult] = None
    platform_results: List[PlatformResult] = field(default_factory=list)
