# -*- coding: utf-8 -*-
"""RPA 运行配置。

这里只放部署环境、运行参数、调试开关这类可配置项。
平台固有常量应放到对应平台代码中。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parents[3]  # 项目根目录


def _load_local_env(path: Path) -> None:
    """加载项目 ``.env`` 中的简单条目，不覆盖进程环境变量。"""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        log.warning("读取 .env 失败: %s", path, exc_info=True)
        return
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


_load_local_env(BASE_DIR / ".env")


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


# ===== 调试 =====
DEBUG_MODE = _env_flag("RPA_DEBUG", "0")

#开发人员调式的输出文件夹
DEBUG_DIR = BASE_DIR / "debug"
#日志输出文件夹
LOG_DIR = BASE_DIR / "logs"
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
