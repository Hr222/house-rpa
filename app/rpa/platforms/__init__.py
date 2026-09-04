# -*- coding: utf-8 -*-
"""平台适配器集合：贝壳、安居客、链家、房天下、乐有家、行舟深房。

每个平台一个聚合目录 platforms/<code>/：shell.py 平台薄壳（城市导航
与流程委托）、collector.py 采集适配、parser.py 纯函数解析、
constants.py 平台常量；base.py 适配器基类，city_map.py 跨平台城市
映射。parser 保持纯函数，浏览器、登录态与风控处理只留在 shell/
collector；平台目录不直写业务数据库。新平台接入见
docs/平台扩展对接文档.md。
"""

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
