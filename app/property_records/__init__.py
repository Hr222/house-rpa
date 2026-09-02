# -*- coding: utf-8 -*-
"""房源成交与挂牌记录模块。"""

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
