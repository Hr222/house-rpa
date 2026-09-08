# -*- coding: utf-8 -*-
"""RPA 采集模型。与平台无关，所有 adapter 共用。"""
from dataclasses import dataclass, field
from typing import List, Optional

from app.rpa.core.status import PlatformResultStatus


@dataclass(slots=True)
class InquiryRequest:
    """后端 → RPA 的请求。

    area: 精确面积（如 89.5）。各平台按自身档位规则自动匹配在售筛选区间，
          该区间同时用于成交记录筛选。
    administrative_district: 行政区；主要供行舟深房本地小区索引消歧。
    """
    community_name: str
    area: float
    city: str = "深圳"
    # 客户端请求标识，仅用于日志和结果关联；内部任务 ID 由 Runtime 单独维护。
    request_id: Optional[str] = None
    # 行政区用于行舟深房 xqData.json 中同名小区的消歧。
    # 保留 None 兼容旧的直接调用和崩溃恢复任务；API 入口要求传入。
    administrative_district: Optional[str] = None
    # URL 白名单直达（编排层为每平台查好入口后携带；RPA 不感知 community_id）。
    # 平台 code -> 小区挂牌列表入口；无该平台 key 表示未初始化入口，平台不采集。
    platform_listing_pages: dict = field(default_factory=dict)
    # 平台 code -> 小区成交列表入口（仅采真实成交的平台需要）。
    platform_deal_pages: dict = field(default_factory=dict)
    # 本次 RPA 需要采集的平台 code 列表；None = 全部注册平台（旧行为/向后
    # 兼容）。热数据短路时编排层只把"冷"平台放进来；目标全热时为空列表
    # （合法值：一个平台都不跑，结果全部来自库内合成）。
    platform_codes: Optional[list] = None


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
    title: Optional[str] = None          # 营销标题（区别于小区名）
    area: Optional[float] = None
    layout: Optional[str] = None
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    listing_url: Optional[str] = None


@dataclass
class PlatformResult:
    """单平台原始结构化采集结果。

    语义边界（算法层已剥离）：RPA 只负责"URL 白名单直达抓取原始数据并回传"，
    ——携带入口 URL（listing_page_url/deal_page_url）与原始明细
    （listing_snapshots 含每套 unit_price/total_price、deal_records 含每笔
    area/date/price），以及页面直接展示的原值（community_avg_price，仅溯源
    不作计算）；quote_prices/deal_prices 不再由 RPA 产出（历史兼容保留字段，
    由编排层 aggregation 从原始明细推导并交给算法层估价）。
    """
    name: str
    status: PlatformResultStatus
    community_avg_price: Optional[float] = None   # 页面卡片原值(元/㎡)，仅溯源
    quote_prices: List[float] = field(default_factory=list)   # 兼容字段：算法层推导，RPA 不填
    deal_prices: List[float] = field(default_factory=list)    # 兼容字段：算法层推导，RPA 不填
    deal_records: List[dict] = field(default_factory=list)     # 成交记录详情 [{area,date,total_price,price},...]（原始全量）
    reason: Optional[str] = None
    request_id: Optional[str] = None
    detail_url: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    listing_snapshots: List[ListingSnapshot] = field(default_factory=list)
    deal_source: str = ""   # 成交来源说明: "成交记录" / "挂牌均价顶替" / "小区均价顶替" / "无"
    listing_page_url: Optional[str] = None   # 小区挂牌销售列表入口（URL 直达采集回填）
    deal_page_url: Optional[str] = None      # 小区成交列表入口（仅采成交平台有值）


@dataclass(slots=True)
class PlatformSession:
    """平台常驻会话。"""
    code: str
    name: str
    start_url: str
    page: object
    ready: bool = False


@dataclass
class RPACollectionResult:
    """一次 RPA 采集完成后交给编排层的原始平台结果。"""

    platform_results: List[PlatformResult] = field(default_factory=list)
