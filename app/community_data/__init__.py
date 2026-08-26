# -*- coding: utf-8 -*-
"""小区基础数据模块的两个对外查询接口。"""

from app.community_data.service import find_nearby_communities, resolve_communities

__all__ = [
    "find_nearby_communities",
    "resolve_communities",
]
