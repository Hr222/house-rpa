# -*- coding: utf-8 -*-
"""一次性将 xqData.json 导入自有 SQLite，并按需补坐标。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import CommunityDatabase
from app.community_data.models import CommunitySeed
from app.community_data.service import CommunityDataService


def load_entries(path: Path) -> list[dict]:
    """读取一次性来源快照。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"读取 xqData.json 失败: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"xqData.json 不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("xqData.json 顶层必须是数组")
    return [entry for entry in payload if isinstance(entry, dict)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一次性初始化小区 SQLite 数据库")
    parser.add_argument("--xq-data", type=Path, required=True, help="一次性导入的 xqData.json")
    parser.add_argument("--city", default="深圳", help="来源快照对应城市，默认深圳")
    parser.add_argument("--database", type=Path, help="SQLite 文件路径")
    parser.add_argument("--limit", type=int, help="只导入前 N 条，用于先做小批验证")
    parser.add_argument(
        "--geocode-limit",
        type=int,
        default=0,
        help="导入后最多调用腾讯地图补 N 条坐标，默认 0，不调用接口",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit 必须是正整数")
    if args.geocode_limit < 0:
        raise SystemExit("--geocode-limit 不能小于 0")

    entries = load_entries(args.xq_data)
    if args.limit is not None:
        entries = entries[: args.limit]

    service = CommunityDataService(database=CommunityDatabase(args.database))
    imported = 0
    skipped = 0
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        administrative_district = str(entry.get("area") or "").strip()
        if not name or not administrative_district:
            skipped += 1
            continue
        aliases = tuple(
            alias.strip()
            for alias in str(entry.get("rename") or "").split(",")
            if alias.strip()
        )
        service.add_seed(
            CommunitySeed(
                city=args.city,
                administrative_district=administrative_district,
                district=str(entry.get("district") or "").strip() or None,
                name=name,
                aliases=aliases,
            )
        )
        imported += 1

    geocoded = service.geocode_pending(args.geocode_limit or None) if args.geocode_limit else 0
    print(f"导入完成: source={len(entries)}, imported={imported}, skipped={skipped}")
    print(f"本次地理编码成功: {geocoded}")
    print(f"数据库: {service.database.path}")
    print("xqData.json 仍保留在原位置；确认数据库和坐标后再手动清理。")


if __name__ == "__main__":
    main()
