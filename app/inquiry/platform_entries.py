# -*- coding: utf-8 -*-
"""平台入口白名单加载（task_manager 与 completion 共用）。

从 property_records.community_platform_pages 读取小区在各平台的
挂牌/成交入口 URL；未初始化入口的平台不在返回结果中，对应平台
采集时直接 NO_DATA（设计行为，见 docs/平台扩展对接文档.md §8）。
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def load_platform_entries(community_id: int) -> tuple[dict, dict]:
    """按小区查各平台挂牌/成交入口，返回 (listing_pages, deal_pages)。

    键为平台 code，值为入口 URL；读取失败的平台记 warning 后跳过
    （视为未初始化，宁可少采不可采错）。
    """
    from app.rpa.registry import build_default_adapters
    from app.property_records.database import PropertyRecordsDatabase

    database = PropertyRecordsDatabase()
    listing_pages: dict[str, str] = {}
    deal_pages: dict[str, str] = {}
    for adapter in build_default_adapters():
        code = adapter.code
        try:
            page = database.get_community_platform_page(community_id, code)
        except Exception as exc:
            log.warning(
                "读取平台入口失败 code=%s community_id=%s: %s", code, community_id, exc
            )
            continue
        if page is None:
            continue
        if page.listing_page_url:
            listing_pages[code] = page.listing_page_url
        if page.deal_page_url:
            deal_pages[code] = page.deal_page_url
    return listing_pages, deal_pages
