# -*- coding: utf-8 -*-
"""平台适配器集合。"""

from app.rpa.platforms.ajk import AjkPlatformAdapter
from app.rpa.platforms.base import PlatformAdapter
from app.rpa.platforms.fang import FangPlatformAdapter
from app.rpa.platforms.ke import KePlatformAdapter
from app.rpa.platforms.lj import LjPlatformAdapter
from app.rpa.platforms.lyj import LyjPlatformAdapter
from app.rpa.platforms.xzsfbj import XzsfbjPlatformAdapter

__all__ = [
    "PlatformAdapter",
    "KePlatformAdapter",
    "AjkPlatformAdapter",
    "FangPlatformAdapter",
    "LjPlatformAdapter",
    "LyjPlatformAdapter",
    "XzsfbjPlatformAdapter",
]
