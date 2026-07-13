# -*- coding: utf-8 -*-
"""平台适配器集合。"""

from app.platforms.ajk import AjkPlatformAdapter
from app.platforms.base import PlatformAdapter
from app.platforms.fang import FangPlatformAdapter
from app.platforms.ke import KePlatformAdapter

__all__ = ["PlatformAdapter", "KePlatformAdapter", "AjkPlatformAdapter", "FangPlatformAdapter"]
