# -*- coding: utf-8 -*-
"""RPA 运行配置。

这里只放部署环境、运行参数、调试开关这类可配置项。
平台固有常量应放到对应平台代码中。
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}

# ===== 调试 =====
DEBUG_MODE = _env_flag("RPA_DEBUG", "0")

BASE_DIR = Path(__file__).resolve().parent
#开发人员调式的输出文件夹
DEBUG_DIR = BASE_DIR / "debug"
#日志输出文件夹
LOG_DIR = BASE_DIR / "logs"

# ===== 浏览器 =====
BROWSER_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# ===== API =====
API_HOST = "127.0.0.1"
API_PORT = 8000

# ===== 风控规避 =====
DETAIL_TAB_LINGER_SECONDS = 60
REQUEST_TIMEOUT = 30
PLATFORM_KEEPALIVE_INTERVAL = 300
PAGE_LINGER_SECONDS = 8.0

# ===== 算法（需求 §3.6） =====
DEAL_DIFF_THRESHOLD = 0.10
NO_DEAL_DISCOUNT = 0.9
