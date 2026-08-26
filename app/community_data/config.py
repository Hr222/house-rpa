# -*- coding: utf-8 -*-
"""小区基础数据模块的部署配置。"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path


log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
NEARBY_RADIUS_ENV = "COMMUNITY_NEARBY_RADIUS_METERS"
DEFAULT_NEARBY_RADIUS_METERS = 10_000.0


def _load_project_env() -> None:
    """读取项目 .env，且不覆盖已设置的进程环境变量。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        log.warning("读取 .env 失败: %s", env_path, exc_info=True)
        return
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        name = name.strip()
        if name:
            os.environ.setdefault(name, value.strip().strip("'\""))


def _positive_float_from_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        log.warning("环境配置 %s=%r 不是有效数字，使用默认值 %s", name, value, default)
        return default
    if not math.isfinite(parsed) or parsed <= 0:
        log.warning("环境配置 %s=%r 必须是有限的正数，使用默认值 %s", name, value, default)
        return default
    return parsed


_load_project_env()
NEARBY_RADIUS_METERS = _positive_float_from_env(
    NEARBY_RADIUS_ENV,
    DEFAULT_NEARBY_RADIUS_METERS,
)
