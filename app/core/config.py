# -*- coding: utf-8 -*-
"""RPA 运行配置。

这里只放部署环境、运行参数、调试开关这类可配置项。
平台固有常量应放到对应平台代码中。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_positive_float(name: str, default: float) -> float:
    """Read a positive float from the environment, falling back on errors."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        log.warning("环境配置 %s=%r 不是有效数字，使用默认值 %.2f", name, value, default)
        return default
    if parsed <= 0:
        log.warning("环境配置 %s=%r 必须大于0，使用默认值 %.2f", name, value, default)
        return default
    return parsed


# ===== 调试 =====
DEBUG_MODE = _env_flag("RPA_DEBUG", "0")

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根目录
#开发人员调式的输出文件夹
DEBUG_DIR = BASE_DIR / "debug"
#日志输出文件夹
LOG_DIR = BASE_DIR / "logs"
#任务持久化文件夹(崩溃兜底)
PERSIST_DIR = BASE_DIR / "persist"

# ===== 浏览器 =====
BROWSER_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ===== API =====
API_HOST = "127.0.0.1"
API_PORT = 8000

# ===== 结果回调（采集完成后主动 POST 推送给客户端，客户端不轮询）=====
# 格式：POST {CALLBACK_URL}/{task_id}，body 为询价结果 JSON。
# 未配置（None / 空）则不推送，客户端可用 GET /inquiries/{taskId} 兜底（受限流约束）。
CALLBACK_URL = os.getenv("RPA_CALLBACK_URL") or None

# ===== GET 查询限流（防止客户端高强度轮询）=====
# 同一 taskId 两次 GET /inquiries/{taskId} 的最小间隔秒数。
# 客户端主要靠回调拿结果，GET 只是偶发兜底，故设下限。
GET_INQUIRY_MIN_INTERVAL = float(os.getenv("RPA_GET_MIN_INTERVAL", "10"))

# ===== 钉钉机器人通知 =====
# 群机器人 webhook 地址（完整 URL），未配置则不发送通知。
# 安全设置建议用"自定义关键词"（关键词设为"风控"或"RPA"），无需加签。
DINGTALK_WEBHOOK_URL = os.getenv("DINGTALK_WEBHOOK_URL") or None

# ===== 风控规避 =====
DETAIL_TAB_LINGER_SECONDS = 15
REQUEST_TIMEOUT = 30
PLATFORM_KEEPALIVE_INTERVAL = 120  # 完整保活间隔（秒）
HEARTBEAT_INTERVAL = 20  # WebSocket 心跳间隔（秒）
PAGE_LINGER_SECONDS = 3.5  # 每页翻页后模拟停留秒数

# ===== 面积弱参考 =====
# 严格面积范围无法形成有效价格峰时，最多向请求面积两侧扩展的容差。
WEAK_AREA_MAX_TOLERANCE = _env_positive_float(
    "RPA_WEAK_AREA_MAX_TOLERANCE",
    20.0,
)

_RUNTIME_FILE = PERSIST_DIR / "runtime.json"

_WEIGHTED_MEDIAN_DISCOUNT_DEFAULT = 0.9
_weighted_median_discount: float = _WEIGHTED_MEDIAN_DISCOUNT_DEFAULT
_weighted_median_discount_loaded = False


def _load_runtime_config() -> dict:
    """从 persist/runtime.json 加载运行时参数，文件不存在返回空 dict。"""
    try:
        if _RUNTIME_FILE.is_file():
            return json.loads(_RUNTIME_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.warning("读取运行时配置失败，回退到默认值", exc_info=True)
    return {}


def _save_runtime_config(data: dict) -> None:
    """写回 persist/runtime.json。"""
    try:
        PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        _RUNTIME_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.warning("写入运行时配置失败", exc_info=True)


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


def _merge_and_save(**fields) -> None:
    """合并写入 persist/runtime.json，保留已有字段。"""
    existing = _load_runtime_config()
    existing.update(fields)
    existing["updatedAt"] = datetime.now().isoformat()
    _save_runtime_config(existing)


def get_weighted_median_discount() -> float:
    """返回加权落点中位数算法的折扣系数。"""
    _ensure_loaded()
    return _weighted_median_discount


def set_weighted_median_discount(value: float) -> float:
    """更新加权落点中位数折扣系数，同时弱持久化到文件。

    Args:
        value: 折扣值，需在 (0, 1) 区间。
    """
    if not (0 < value < 1):
        raise ValueError(f"weightedMedianDiscount 必须在 (0, 1) 区间，收到 {value}")

    global _weighted_median_discount, _weighted_median_discount_loaded
    _ensure_loaded()
    _weighted_median_discount = value
    _weighted_median_discount_loaded = True
    _merge_and_save(weightedMedianDiscount=value)
    log.info("weightedMedianDiscount 已更新为 %.4f", value)
    return _weighted_median_discount


def is_weighted_median_discount_default() -> bool:
    """当前 weightedMedianDiscount 是否还是出厂默认值。"""
    _ensure_loaded()
    return _weighted_median_discount == _WEIGHTED_MEDIAN_DISCOUNT_DEFAULT


# 当前算法使用的折扣系数。
WEIGHTED_MEDIAN_DISCOUNT = _WEIGHTED_MEDIAN_DISCOUNT_DEFAULT
