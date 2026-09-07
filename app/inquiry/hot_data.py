# -*- coding: utf-8 -*-
"""热数据判定：48h 内更新过的小区×平台数据直接复用，跳过 RPA 采集。

热度来源是 listing_records.updated_at（is_deleted=0 行）按小区×平台取
MAX——落库本来就是"一个小区一个平台整批更新"，这个 MAX 即该平台上次
采集时间。没有在架行的平台自然为冷（NO_DATA 不算热，重新走 RPA）。

激活门槛：同一小区命中热数据的平台数 ≥ RPA_HOT_DATA_MIN_PLATFORMS
（默认 3）才启用热路径；不足则全部走 RPA，换一次全平台新鲜快照。
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

HOT_DATA_MAX_AGE_ENV = "RPA_HOT_DATA_MAX_AGE_HOURS"
HOT_DATA_MIN_PLATFORMS_ENV = "RPA_HOT_DATA_MIN_PLATFORMS"
DEFAULT_MAX_AGE_HOURS = 48.0
DEFAULT_MIN_PLATFORMS = 3


def get_hot_data_max_age_hours() -> float:
    """热数据窗口（小时）；每次实时读取，改环境变量重启后生效。"""
    raw = os.getenv(HOT_DATA_MAX_AGE_ENV)
    if raw is None:
        return DEFAULT_MAX_AGE_HOURS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warning("%s=%r 不是有效数字，使用默认 %s", HOT_DATA_MAX_AGE_ENV, raw, DEFAULT_MAX_AGE_HOURS)
        return DEFAULT_MAX_AGE_HOURS
    if not math.isfinite(value) or value <= 0:
        log.warning("%s=%r 必须是正数，使用默认 %s", HOT_DATA_MAX_AGE_ENV, raw, DEFAULT_MAX_AGE_HOURS)
        return DEFAULT_MAX_AGE_HOURS
    return value


def get_hot_data_min_platforms() -> int:
    """激活热路径需要的最少热平台数；不足该数全部走 RPA。"""
    raw = os.getenv(HOT_DATA_MIN_PLATFORMS_ENV)
    if raw is None:
        return DEFAULT_MIN_PLATFORMS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("%s=%r 不是有效整数，使用默认 %s", HOT_DATA_MIN_PLATFORMS_ENV, raw, DEFAULT_MIN_PLATFORMS)
        return DEFAULT_MIN_PLATFORMS
    if value <= 0:
        log.warning("%s=%r 必须大于 0，使用默认 %s", HOT_DATA_MIN_PLATFORMS_ENV, raw, DEFAULT_MIN_PLATFORMS)
        return DEFAULT_MIN_PLATFORMS
    return value


def select_hot_platforms(
    freshness: dict[str, str],
    platform_codes: list[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    """从各平台最近更新时间里挑出热平台。

    Args:
        freshness: 平台 code -> 最近更新时间（ISO-8601 文本，来自
            listing_records.updated_at 的 MAX，缺时区按 UTC）。
        platform_codes: 参与判定的平台 code（通常为有入口的平台）。
        now: 注入当前时间，测试用。

    Returns:
        命中热窗口的平台 code 列表；不足激活门槛时返回空列表（全冷）。
    """
    window = timedelta(hours=get_hot_data_max_age_hours())
    current = now or datetime.now(timezone.utc)
    hot: list[str] = []
    for code in platform_codes:
        raw = freshness.get(code)
        if not raw:
            continue
        try:
            last_update = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            log.warning("平台[%s] 最近更新时间无法解析：%r，视为冷", code, raw)
            continue
        if last_update.tzinfo is None:
            last_update = last_update.replace(tzinfo=timezone.utc)
        if current - last_update <= window:
            hot.append(code)
    if len(hot) < get_hot_data_min_platforms():
        return []
    return sorted(hot)
