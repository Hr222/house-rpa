# -*- coding: utf-8 -*-
"""跨平台城市映射表。

5 个平台 URL 前缀命名规则完全不统一（缩写/全拼混用），
不能用规则推导，必须维护显式映射表。

数据来源：从各平台城市选择页 HTML dump 中提取（2026-07-17）。

广东 21 个地级市：
广州、深圳、珠海、汕头、佛山、韶关、湛江、肇庆、江门、茂名、
惠州、梅州、汕尾、河源、阳江、清远、东莞、中山、潮州、揭阳、云浮

各平台覆盖数：ajk 21/21、fang 21/21、ke 12/21、lj 10/21、lyj 9/21
5 平台全部覆盖的城市（9 个）：
广州、深圳、珠海、佛山、东莞、中山、惠州、江门、清远
"""

from __future__ import annotations

from typing import Optional


# CITY_MAP[platform_code][city_name] = url_prefix
CITY_MAP: dict[str, dict[str, str]] = {
    # 贝壳：缩写为主，jiangmen/zhanjiang/yangjiang 用全拼
    "ke": {
        "广州": "gz",
        "深圳": "sz",
        "珠海": "zh",
        "佛山": "fs",
        "江门": "jiangmen",
        "湛江": "zhanjiang",
        "肇庆": "zq",
        "惠州": "hui",
        "阳江": "yangjiang",
        "东莞": "dg",
        "中山": "zs",
        "清远": "qy",
    },
    # 链家：与贝壳共用同一套命名，但覆盖城市更少
    "lj": {
        "广州": "gz",
        "深圳": "sz",
        "珠海": "zh",
        "佛山": "fs",
        "惠州": "hui",
        "江门": "jiangmen",
        "清远": "qy",
        "东莞": "dg",
        "中山": "zs",
        "湛江": "zhanjiang",
    },
    # 安居客：全拼为主，dg/zh/zs 用缩写，云浮是 yufu（非 yunfu）
    "ajk": {
        "广州": "guangzhou",
        "深圳": "shenzhen",
        "珠海": "zh",
        "汕头": "shantou",
        "佛山": "foshan",
        "韶关": "shaoguan",
        "湛江": "zhanjiang",
        "肇庆": "zhaoqing",
        "江门": "jiangmen",
        "茂名": "maoming",
        "惠州": "huizhou",
        "梅州": "meizhou",
        "汕尾": "shanwei",
        "河源": "heyuan",
        "阳江": "yangjiang",
        "清远": "qingyuan",
        "东莞": "dg",
        "中山": "zs",
        "潮州": "chaozhou",
        "揭阳": "jieyang",
        "云浮": "yufu",
    },
    # 房天下：缩写和全拼混用，gz/sz/zh/fs/dg/zs/st/jm/zj 是缩写
    "fang": {
        "广州": "gz",
        "深圳": "sz",
        "珠海": "zh",
        "汕头": "st",
        "佛山": "fs",
        "韶关": "shaoguan",
        "湛江": "zj",
        "肇庆": "zhaoqing",
        "江门": "jm",
        "茂名": "maoming",
        "惠州": "huizhou",
        "梅州": "meizhou",
        "汕尾": "shanwei",
        "河源": "heyuan",
        "阳江": "yangjiang",
        "清远": "qingyuan",
        "东莞": "dg",
        "中山": "zs",
        "潮州": "chaozhou",
        "揭阳": "jieyang",
        "云浮": "yunfu",
    },
    # 乐有家：统一全拼，仅 9 城
    "lyj": {
        "深圳": "shenzhen",
        "广州": "guangzhou",
        "珠海": "zhuhai",
        "佛山": "foshan",
        "东莞": "dongguan",
        "中山": "zhongshan",
        "惠州": "huizhou",
        "江门": "jiangmen",
        "清远": "qingyuan",
    },
}

# 各平台 URL 模板
_URL_PATTERNS: dict[str, str] = {
    "ke": "https://{prefix}.ke.com/ershoufang/",
    "lj": "https://{prefix}.lianjia.com/ershoufang/",
    "ajk": "https://{prefix}.anjuke.com/sale/",
    "fang": "https://{prefix}.esf.fang.com/",
    "lyj": "https://{prefix}.leyoujia.com/esf/",
}


def get_start_url(platform_code: str, city: str) -> str:
    """根据平台代码和城市名获取该平台该城市的二手房起始 URL。

    Args:
        platform_code: 平台代码（ke/lj/ajk/fang/lyj）。
        city: 城市中文名（如"深圳"、"广州"）。

    Returns:
        完整的起始 URL。

    Raises:
        ValueError: 平台不支持该城市时抛出。
    """
    city_map = CITY_MAP.get(platform_code)
    if not city_map:
        raise ValueError(f"未知平台代码: {platform_code}")

    prefix = city_map.get(city)
    if not prefix:
        supported = "、".join(sorted(city_map.keys()))
        raise ValueError(f"平台 {platform_code} 不支持城市「{city}」，支持: {supported}")

    return _URL_PATTERNS[platform_code].format(prefix=prefix)


def is_city_supported(platform_code: str, city: str) -> bool:
    """检查平台是否支持指定城市。"""
    city_map = CITY_MAP.get(platform_code)
    return bool(city_map and city_map.get(city))
