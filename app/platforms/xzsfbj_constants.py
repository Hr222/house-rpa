# -*- coding: utf-8 -*-
"""行舟深房小程序接口平台固有常量。"""

from __future__ import annotations

from pathlib import Path

from app.core import config


BASE_URL = "https://www.xzsfbj.com.cn"
START_URL = "about:blank"
API_DEALS_PATH = "/api/house/getXqDeal"
API_SALES_PATH = "/api/house/getCommunitySales"

AES_KEY_ENV = "XZSFBJ_AES_KEY"
MINI_PROGRAM_APP_ID = "wxd49effb77288061d"
WMPF_WS_URL = "ws://127.0.0.1:62000"
WMPF_BRIDGE_DIR_ENV = "XZSFBJ_WMPF_BRIDGE_DIR"
DEFAULT_WMPF_BRIDGE_DIR = config.BASE_DIR / "third_party" / "zhong_wmpf_bridge"
XQ_DATA_PATH_ENV = "XZSFBJ_XQ_DATA_PATH"
LEGACY_XQ_DATA_PATH_ENV = "XQ_DATA_PATH"

TOKEN_CAPTURE_TIMEOUT = 30.0
MAX_COMMUNITIES_PER_TOKEN = 30
TOKEN_REFRESH_INTERVAL = 3600.0
PAGE_GAP_MIN = 11.0
PAGE_GAP_MAX = 11.0
COMMUNITY_GAP_MIN = 11.0
COMMUNITY_GAP_MAX = 11.0
DEAL_AREA_TOLERANCE = 1.0
DEAL_LOOKBACK_MONTHS = 6
DEAL_PAGE_SIZE = 30
DEAL_PAGE_RISK_CODES = frozenset({"40002"})
DEAL_PAGE_RISK_RETRY_DELAY = 11.0
DEAL_PAGE_RISK_MAX_RETRIES = 3

# xqData 没有独立的用途字段时，只排除名称中明确标注为非住宅的条目；
# 住宅项目名称中出现“大厦/中心”等字样不能单凭名称判为非住宅。
NON_RESIDENTIAL_NAME_MARKERS = (
    "商铺",
    "写字楼",
    "办公楼",
    "商务公寓",
    "商业公寓",
    "酒店",
    "宿舍",
    "厂房",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF"
)
REFERER = f"https://servicewechat.com/{MINI_PROGRAM_APP_ID}/56/page-frame.html"

RISK_KEYWORDS = (
    "风控", "限制", "封", "禁止", "block", "risk", "deny", "forbidden", "频繁",
)
LOGIN_ERROR_CODES = frozenset({"40001", "41000"})
SENSITIVE_DEBUG_KEYS = ("authorization", "token", "cookie", "password")


def get_aes_key() -> bytes:
    """Read and validate the xzsfbj regionId encryption key from the environment.

    The key is intentionally not kept in source code. ``app.core.config`` loads
    the project ``.env`` before platform constants are imported.
    """
    import os

    value = os.environ.get(AES_KEY_ENV, "").strip()
    if not value:
        raise RuntimeError(
            f"未配置行舟深房加密密钥，请在 .env 中设置 {AES_KEY_ENV}"
        )
    key = value.encode("utf-8")
    if len(key) not in {16, 24, 32}:
        raise RuntimeError(
            f"{AES_KEY_ENV} 必须是 16、24 或 32 字节，当前为 {len(key)} 字节"
        )
    return key


def resolve_wmpf_bridge_dir() -> Path:
    """返回环境变量指定或项目内置的 WMPF 调试桥目录。"""
    import os

    configured = os.environ.get(WMPF_BRIDGE_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_WMPF_BRIDGE_DIR


def resolve_xq_data_file() -> Path:
    """定位当前 Windows 微信用户的小程序小区索引。"""
    import os

    configured = os.environ.get(XQ_DATA_PATH_ENV) or os.environ.get(LEGACY_XQ_DATA_PATH_ENV)
    if configured:
        return Path(configured).expanduser().resolve()

    appdata = os.environ.get("APPDATA")
    users_dir = Path(appdata or ".") / "Tencent" / "xwechat" / "radium" / "users"
    candidates = list(users_dir.glob(
        f"*/applet/local/{MINI_PROGRAM_APP_ID}/usr/xqData.json"
    ))
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    return (
        users_dir / "<current-user>" / "applet" / "local" /
        MINI_PROGRAM_APP_ID / "usr" / "xqData.json"
    )
