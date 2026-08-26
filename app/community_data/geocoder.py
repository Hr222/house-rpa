# -*- coding: utf-8 -*-
"""腾讯地图地理编码客户端。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GEOCODER_URL = "https://apis.map.qq.com/ws/geocoder/v1/"
SIGN_PATH = "/ws/geocoder/v1/"
API_KEY_ENV = "TENCENT_MAP_API_KEY"
API_SK_ENV = "TENCENT_MAP_API_SK"


def _load_project_env() -> None:
    """读取项目 .env，不覆盖已有进程环境变量。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            os.environ.setdefault(name, value.strip("'\""))


@dataclass(frozen=True)
class GeocodeResult:
    """腾讯地图返回的坐标和质量信息。"""

    longitude: float
    latitude: float
    level: int | None
    reliability: int | None
    coordinate_system: str = "GCJ-02"


class TencentGeocoder:
    """腾讯地图地址解析客户端。"""

    def __init__(
        self,
        key: str | None = None,
        secret_key: str | None = None,
        timeout: float = 10.0,
        load_env: bool = True,
    ) -> None:
        if load_env:
            _load_project_env()
        self.key = (key or os.getenv(API_KEY_ENV, "")).strip()
        configured_secret_key = (
            secret_key if secret_key is not None else os.getenv(API_SK_ENV, "")
        )
        self.secret_key = configured_secret_key.strip() or None
        self.timeout = timeout

    def build_url(self, address: str) -> str:
        """构造请求 URL；签名原串保留未编码参数，实际 URL 参数编码。"""
        if not self.key:
            raise RuntimeError(f"请先配置 {API_KEY_ENV}")
        params = {"address": address, "key": self.key}
        sorted_params = sorted(params.items())
        raw_query = "&".join(f"{name}={value}" for name, value in sorted_params)
        request_query = urlencode(sorted_params)
        if self.secret_key:
            sign_source = f"{SIGN_PATH}?{raw_query}{self.secret_key}"
            signature = hashlib.md5(sign_source.encode("utf-8")).hexdigest()
            request_query += f"&sig={signature}"
        return f"{GEOCODER_URL}?{request_query}"

    def geocode(self, address: str) -> GeocodeResult:
        """调用腾讯地图获取 GCJ-02 坐标。"""
        request_url = self.build_url(address)
        with urlopen(request_url, timeout=self.timeout) as response:
            payload: dict[str, Any] = json.loads(
                response.read().decode("utf-8")
            )
        if payload.get("status") != 0:
            raise RuntimeError(
                "腾讯地图地理编码失败: "
                f"status={payload.get('status')}, message={payload.get('message', '')}"
            )
        try:
            result = payload["result"]
            location = result["location"]
            return GeocodeResult(
                longitude=float(location["lng"]),
                latitude=float(location["lat"]),
                level=_optional_int(result.get("level")),
                reliability=_optional_int(result.get("reliability")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("腾讯地图返回结果缺少有效坐标") from exc


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
