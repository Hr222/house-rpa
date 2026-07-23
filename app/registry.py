# -*- coding: utf-8 -*-
"""平台注册表。"""

from app.platforms import (
    AjkPlatformAdapter,
    FangPlatformAdapter,
    KePlatformAdapter,
    LjPlatformAdapter,
    LyjPlatformAdapter,
    PlatformAdapter,
)


def build_default_adapters() -> list[PlatformAdapter]:
    """默认启用的平台列表。"""
    return [
        KePlatformAdapter(),
        AjkPlatformAdapter(),
        FangPlatformAdapter(),
        LjPlatformAdapter(),
        LyjPlatformAdapter(),
    ]
