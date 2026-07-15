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

# ===== 结果回调（采集完成后主动通知客户端，客户端不轮询）=====
# 格式：POST {CALLBACK_URL}/{task_id}，body 为询价结果 JSON。
# 目前未定，先用占位符，后续客户端接口确定后填入。
CALLBACK_URL = None

# ===== 风控规避 =====
DETAIL_TAB_LINGER_SECONDS = 15
REQUEST_TIMEOUT = 30
PLATFORM_KEEPALIVE_INTERVAL = 120  # 完整保活间隔（秒）
HEARTBEAT_INTERVAL = 20  # WebSocket 心跳间隔（秒）
PAGE_LINGER_SECONDS = 3.5  # 每页翻页后模拟停留秒数

# ===== 算法（需求 §3.6） =====
# 成交价分歧阈值：|在售均价 - 成交均价| / 成交均价
#   ≤ DEAL_DIFF_THRESHOLD → 取两者中较低值 (TAKE_LOWER)
#   > DEAL_DIFF_THRESHOLD → 只取成交均价 (DEAL_ONLY)
DEAL_DIFF_THRESHOLD = 0.10

# ---- 无成交折扣（弱持久化，重启不丢失）----

_RUNTIME_FILE = PERSIST_DIR / "runtime.json"

_NO_DEAL_DISCOUNT_DEFAULT = 0.9
_no_deal_discount: float = _NO_DEAL_DISCOUNT_DEFAULT
_no_deal_discount_loaded = False


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
    global _no_deal_discount, _no_deal_discount_loaded
    if _no_deal_discount_loaded:
        return
    data = _load_runtime_config()
    if "noDealDiscount" in data:
        try:
            value = float(data["noDealDiscount"])
            if 0 < value < 1:
                _no_deal_discount = value
                log.info("从持久化恢复 noDealDiscount=%.4f", _no_deal_discount)
            else:
                log.warning("持久化的 noDealDiscount=%.4f 不合法，使用默认值 0.9", value)
        except (TypeError, ValueError):
            log.warning("持久化的 noDealDiscount 解析失败，使用默认值 0.9")
    _no_deal_discount_loaded = True


def get_no_deal_discount() -> float:
    """返回当前生效的无成交折扣。"""
    _ensure_loaded()
    return _no_deal_discount


def set_no_deal_discount(value: float) -> float:
    """更新无成交折扣，同时弱持久化到文件。

    Args:
        value: 折扣值，需在 (0, 1) 区间。

    Returns:
        更新后的值。

    Raises:
        ValueError: 值不在合法区间。
    """
    if not (0 < value < 1):
        raise ValueError(f"noDealDiscount 必须在 (0, 1) 区间，收到 {value}")

    global _no_deal_discount, _no_deal_discount_loaded
    _ensure_loaded()
    _no_deal_discount = value
    _no_deal_discount_loaded = True
    _save_runtime_config({
        "noDealDiscount": value,
        "updatedAt": datetime.now().isoformat(),
    })
    log.info("noDealDiscount 已更新为 %.4f", value)
    return _no_deal_discount


def is_no_deal_discount_default() -> bool:
    """当前值是否还是出厂默认值（未被人为修改过）。"""
    _ensure_loaded()
    return _no_deal_discount == _NO_DEAL_DISCOUNT_DEFAULT


# 兼容旧代码：模块导入时的别名（但不推荐再用，应走 getter）
NO_DEAL_DISCOUNT = _NO_DEAL_DISCOUNT_DEFAULT
