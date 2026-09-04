# -*- coding: utf-8 -*-
"""房源成交与挂牌记录模块：接收 RPA 已解析结果，标准化后入库归档。

负责：把 RPA 已解析的成交和挂牌结果写入 SQLite
（PropertyRecordsIngestion），提供 PropertyRecordsDatabase 数据访问
与成交/挂牌/小区平台页等模型。
不负责：浏览器采集本身，也不参与询价编排（详见 docs/房源记录模块.md）。

内部：ingestion.py 采集结果入库 | database.py SQLite 数据访问 |
normalization.py 日期/金额/面积/URL 标准化 | models.py 数据模型
"""

from app.property_records.database import PropertyRecordsDatabase
from app.property_records.ingestion import (
    IngestionReport,
    PropertyRecordsIngestion,
    normalize_source_platform,
)
from app.property_records.models import (
    CommunityPlatformPage,
    DealRecord,
    ListingRecord,
    ListingRecordLog,
    UniqueDealRecord,
)

__all__ = [
    "CommunityPlatformPage",
    "DealRecord",
    "ListingRecord",
    "ListingRecordLog",
    "UniqueDealRecord",
    "PropertyRecordsDatabase",
    "IngestionReport",
    "PropertyRecordsIngestion",
    "normalize_source_platform",
]
