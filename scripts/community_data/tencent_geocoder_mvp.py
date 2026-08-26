# -*- coding: utf-8 -*-
"""腾讯地图地理编码 MVP：验证单个深圳小区地址可获取坐标。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GEOCODER_URL = "https://apis.map.qq.com/ws/geocoder/v1/"
API_KEY_ENV = "TENCENT_MAP_API_KEY"
API_SK_ENV = "TENCENT_MAP_API_SK"
XQ_DATA_ENV = "XZSFBJ_XQ_DATA_PATH"
LEGACY_XQ_DATA_ENV = "XQ_DATA_PATH"
MINI_PROGRAM_APP_ID = "wxd49effb77288061d"
DEFAULT_ADDRESS = "深圳市罗湖区城市天地广场"
SIGN_PATH = "/ws/geocoder/v1/"


def _load_project_env() -> None:
    """读取项目 .env，不覆盖已有进程环境变量。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            os.environ.setdefault(name, value.strip("'\""))


def resolve_xq_data_file() -> Path:
    """定位本机微信小程序 xqData.json；也支持环境变量显式指定。"""
    configured = os.getenv(XQ_DATA_ENV) or os.getenv(LEGACY_XQ_DATA_ENV)
    if configured:
        return Path(configured).expanduser().resolve()

    appdata = os.getenv("APPDATA")
    users_dir = Path(appdata or ".") / "Tencent" / "xwechat" / "radium" / "users"
    candidates = list(users_dir.glob(
        f"*/applet/local/{MINI_PROGRAM_APP_ID}/usr/xqData.json"
    ))
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    return users_dir / "<current-user>" / "applet" / "local" / MINI_PROGRAM_APP_ID / "usr" / "xqData.json"


_load_project_env()


def _build_url(address: str, key: str, secret_key: str | None = None) -> str:
    """构造腾讯地图 WebService 地理编码请求 URL。"""
    params = {"address": address, "key": key}
    sorted_params = sorted(params.items())
    raw_query = "&".join(f"{name}={value}" for name, value in sorted_params)
    request_query = urlencode(sorted_params)
    if secret_key:
        signature_source = f"{SIGN_PATH}?{raw_query}{secret_key}"
        signature = hashlib.md5(signature_source.encode("utf-8")).hexdigest()
        request_query += f"&sig={signature}"
    return f"{GEOCODER_URL}?{request_query}"


def geocode(address: str) -> dict[str, Any]:
    """请求腾讯地图并返回完整响应；根据当前 Key 配置自动选择签名模式。"""
    key = os.getenv(API_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(f"请先在 .env 配置 {API_KEY_ENV}")

    secret_key = os.getenv(API_SK_ENV, "").strip() or None
    request_url = _build_url(address, key, secret_key)
    with urlopen(request_url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("status") != 0:
        raise RuntimeError(
            "腾讯地图地理编码失败: "
            f"status={payload.get('status')}, message={payload.get('message', '')}"
        )
    return payload


def find_community_address(
    community_name: str,
    administrative_district: str | None,
    xq_data_path: Path,
) -> str:
    """在本地 xqData 快照中定位小区，拼出优先带行政区的深圳地址。"""
    try:
        entries = json.loads(xq_data_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"读取 xqData.json 失败: {xq_data_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"xqData.json 不是合法 JSON: {xq_data_path}: {exc}") from exc

    if not isinstance(entries, list):
        raise RuntimeError(f"xqData.json 顶层不是小区数组: {xq_data_path}")

    exact_matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("name") == community_name
    ]
    if administrative_district:
        exact_matches = [
            entry for entry in exact_matches
            if entry.get("area") == administrative_district
        ]

    if len(exact_matches) != 1:
        hint = "；请补充 --district" if not administrative_district else ""
        raise RuntimeError(
            f"xqData 中 {community_name!r} 匹配到 {len(exact_matches)} 条记录{hint}"
        )

    entry = exact_matches[0]
    district = entry.get("area")
    name = entry.get("name")
    if not isinstance(district, str) or not isinstance(name, str):
        raise RuntimeError(f"xqData 条目缺少 area/name: {entry!r}")
    return f"深圳市{district}{name}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="腾讯地图深圳小区地理编码 MVP")
    parser.add_argument("--address", help="直接查询的完整地址")
    parser.add_argument("--community", help="从 xqData.json 查询的小区正式名称")
    parser.add_argument("--district", help="小区所在行政区，例如罗湖区")
    parser.add_argument(
        "--xq-data",
        type=Path,
        default=resolve_xq_data_file(),
        help="xqData.json 路径；使用 --community 时生效",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.address and args.community:
        raise SystemExit("--address 与 --community 只能选择一个")

    if args.community:
        address = find_community_address(
            args.community,
            args.district,
            args.xq_data,
        )
    else:
        address = args.address or DEFAULT_ADDRESS

    payload = geocode(address)
    result = payload["result"]
    location = result["location"]
    print(f"地址: {address}")
    print(f"经度 lng: {location['lng']}")
    print(f"纬度 lat: {location['lat']}")
    print(f"匹配层级: {result.get('level')}")
    print(f"可靠度: {result.get('reliability')}")
    print("坐标系: GCJ-02")


if __name__ == "__main__":
    main()
