# -*- coding: utf-8 -*-
"""平台注册表。"""

from app.platforms import AjkPlatformAdapter, FangPlatformAdapter, KePlatformAdapter, PlatformAdapter


def build_default_adapters() -> list[PlatformAdapter]:
    """默认启用的平台列表。后续在这里追加链家/乐有家。"""
    return [KePlatformAdapter(), AjkPlatformAdapter(), FangPlatformAdapter()]
