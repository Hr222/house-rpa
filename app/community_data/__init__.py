# -*- coding: utf-8 -*-
"""小区基础数据模块：人工维护的小区组、期数、别名、建成年份与坐标。

负责：resolve_communities 按正式名或别名查询小区，
find_nearby_communities 查询附近小区（默认 10km、建成年份 ±5 年）；
数据落 persist/community_data.sqlite3。
不负责：浏览器运行时、Runtime 状态机、平台健康状态、询价任务持久化，
也不是 FastAPI 路由（详见 docs/小区基础数据模块.md）。

内部：service.py 对外查询服务 | database.py SQLite 读取 |
geocoder.py 腾讯地图地理编码 | normalization.py 名称规范化与期数
分组 | models.py 数据模型 | config.py 部署配置
"""

from app.community_data.service import find_nearby_communities, resolve_communities

__all__ = [
    "find_nearby_communities",
    "resolve_communities",
]
