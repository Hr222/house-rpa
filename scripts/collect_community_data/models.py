# -*- coding: utf-8 -*-
"""小区数据抓取的原始结果对象：一个对象，两个 list。

listings / deals 直接使用工程对象与入库键名，不另设 schema：
  - listings: 工程 ListingSnapshot（detail_url 为详情链接占位字段，
    提取逻辑待真实页面核对后补，字段随对象提升进工程时一并加入）；
  - deals: 字典 {area, date, total_price(万), price(元/㎡)}，仅 lj/fang，
    键名与入库层 deal_records 完全对齐。
提升进工程时本对象并入 PlatformResult（补 community_id 与两跳 URL 字段）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.rpa.core.models import ListingSnapshot
from app.rpa.core.status import PlatformResultStatus


@dataclass
class CommunityCollection:
    """单平台 × 单小区的原始采集结果（一个对象，两个 list）。"""

    platform: str
    community_id: Optional[int]
    community_name: str                     # 平台页面展示名（记录层 source_community_name）
    listing_page_url: str
    deal_page_url: Optional[str] = None     # 仅 lj/fang 有值
    status: str = PlatformResultStatus.SUCCESS
    blocked_reason: Optional[str] = None
    canonical_name: str = ""                # 主数据正式名（编排层回填）
    listings: list[ListingSnapshot] = field(default_factory=list)   # 挂牌 list
    deals: list[dict] = field(default_factory=list)                 # 成交 list（仅 lj/fang）
    community_avg_price: Optional[float] = None                     # 平台卡片挂牌均价（ajk 有）
