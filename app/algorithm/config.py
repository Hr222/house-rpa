# -*- coding: utf-8 -*-
"""询价算法的运行参数。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path


log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
PERSIST_DIR = BASE_DIR / "persist"
_RUNTIME_FILE = PERSIST_DIR / "runtime.json"

_WEIGHTED_MEDIAN_DISCOUNT_DEFAULT = 0.9
_weighted_median_discount: float = _WEIGHTED_MEDIAN_DISCOUNT_DEFAULT
_weighted_median_discount_loaded = False


def _load_runtime_config() -> dict:
    """从既有 runtime.json 读取算法运行参数。"""
    try:
        if _RUNTIME_FILE.is_file():
            return json.loads(_RUNTIME_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.warning("读取算法运行时配置失败，回退到默认值", exc_info=True)
    return {}


def _save_runtime_config(data: dict) -> None:
    """写回既有 runtime.json，保留其他模块字段。"""
    try:
        PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        _RUNTIME_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.warning("写入算法运行时配置失败", exc_info=True)


def _ensure_loaded() -> None:
    global _weighted_median_discount, _weighted_median_discount_loaded
    if _weighted_median_discount_loaded:
        return
    data = _load_runtime_config()
    stored_value = data.get("weightedMedianDiscount")
    if stored_value is not None:
        try:
            value = float(stored_value)
            if 0 < value < 1:
                _weighted_median_discount = value
                log.info("从持久化恢复 weightedMedianDiscount=%.4f", _weighted_median_discount)
            else:
                log.warning("持久化的 weightedMedianDiscount=%.4f 不合法，使用默认值 0.9", value)
        except (TypeError, ValueError):
            log.warning("持久化的 weightedMedianDiscount 解析失败，使用默认值 0.9")
    _weighted_median_discount_loaded = True


def get_weighted_median_discount() -> float:
    """返回加权落点中位数算法的折扣系数。"""
    _ensure_loaded()
    return _weighted_median_discount


def set_weighted_median_discount(value: float) -> float:
    """更新折扣系数，并保持现有 runtime.json 键兼容。"""
    if not (0 < value < 1):
        raise ValueError(f"weightedMedianDiscount 必须在 (0, 1) 区间，收到 {value}")

    global _weighted_median_discount, _weighted_median_discount_loaded
    _ensure_loaded()
    _weighted_median_discount = value
    _weighted_median_discount_loaded = True
    data = _load_runtime_config()
    data.update(
        weightedMedianDiscount=value,
        updatedAt=datetime.now().isoformat(),
    )
    _save_runtime_config(data)
    log.info("weightedMedianDiscount 已更新为 %.4f", value)
    return _weighted_median_discount


def is_weighted_median_discount_default() -> bool:
    """当前 weightedMedianDiscount 是否还是出厂默认值。"""
    _ensure_loaded()
    return _weighted_median_discount == _WEIGHTED_MEDIAN_DISCOUNT_DEFAULT


WEIGHTED_MEDIAN_DISCOUNT = _WEIGHTED_MEDIAN_DISCOUNT_DEFAULT


def get_weak_area_max_tolerance() -> float:
    """返回弱引用允许的最大面积容差。"""
    raw_value = os.getenv("RPA_WEAK_AREA_MAX_TOLERANCE", "20")
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        log.warning("RPA_WEAK_AREA_MAX_TOLERANCE=%r 不是有效数字，使用 20", raw_value)
        return 20.0
    if value <= 0:
        log.warning("RPA_WEAK_AREA_MAX_TOLERANCE=%r 必须大于 0，使用 20", raw_value)
        return 20.0
    return value
