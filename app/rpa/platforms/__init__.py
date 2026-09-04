# -*- coding: utf-8 -*-
"""平台适配器集合（平台聚合目录，薄壳位于 platforms/<code>/shell.py）。"""

from app.rpa.platforms.base import PlatformAdapter
from app.rpa.platforms.ajk.shell import AjkPlatformAdapter
from app.rpa.platforms.fang.shell import FangPlatformAdapter
from app.rpa.platforms.ke.shell import KePlatformAdapter
from app.rpa.platforms.lj.shell import LjPlatformAdapter
from app.rpa.platforms.lyj.shell import LyjPlatformAdapter
from app.rpa.platforms.xzsfbj.shell import XzsfbjPlatformAdapter

__all__ = [
    "PlatformAdapter",
    "KePlatformAdapter",
    "AjkPlatformAdapter",
    "FangPlatformAdapter",
    "LjPlatformAdapter",
    "LyjPlatformAdapter",
    "XzsfbjPlatformAdapter",
]
