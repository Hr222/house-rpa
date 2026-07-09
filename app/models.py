# -*- coding: utf-8 -*-
"""数据模型。与平台无关，所有 adapter 共用。"""
from typing import List, Optional, Literal
from dataclasses import dataclass, field


class InquiryRequest:
    """后端 → RPA 的请求。"""
    def __init__(self, community_name: str, area_min: float, area_max: float,
                 city: str = "深圳"):
        self.community_name = community_name
        self.area_min = area_min
        self.area_max = area_max
        self.city = city


@dataclass
class PlatformResult:
    """单平台采集结果。"""
    name: str
    status: str  # SUCCESS / NO_DATA / BLOCKED / LOGIN_EXPIRED / ERROR
    community_avg_price: Optional[float] = None   # 详情页小区均价(元/㎡) = P_quote
    quote_prices: List[float] = field(default_factory=list)   # 在售单价列表
    deal_prices: List[float] = field(default_factory=list)    # 成交单价列表(筛选后)
    reason: Optional[str] = None


@dataclass
class InquiryResult:
    """询价最终结果。"""
    success: bool
    final_price: Optional[float] = None    # 最终建议单价(元/㎡)
    branch: str = "FAILED"                  # TAKE_LOWER / DEAL_ONLY / QUOTE_DISCOUNT / FAILED
    quote_avg: Optional[float] = None
    deal_avg: Optional[float] = None
    platform: Optional[PlatformResult] = None
