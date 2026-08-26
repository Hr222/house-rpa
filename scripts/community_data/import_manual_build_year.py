# -*- coding: utf-8 -*-
"""将人工确认的建成年份写入 SQLite，并保留可审计结果日志。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import CommunityDatabase


DEFAULT_RESULTS_PATH = (
    PROJECT_ROOT
    / "outputs"
    / "xqdata_excel_20260824"
    / "build_year_manual_confirmations.jsonl"
)
SOURCE_NAME = "MANUAL_FANG_ANJUKE"
SOURCE_EVIDENCE = "人工确认：以房天下、安居客一致的建成年份为准"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_confirmations(path: Path) -> list[tuple[int, int]]:
    """读取由表格导出的非空 ``id`` 与 ``build_year``。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"读取人工确认文件失败: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"人工确认文件不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("人工确认文件顶层必须是数组")

    values: list[tuple[int, int]] = []
    seen_ids: set[int] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            community_id = int(item["id"])
            build_year = int(item["build_year"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"人工确认记录缺少合法 id/build_year: {item}") from exc
        if community_id <= 0 or not 1800 <= build_year <= 2026:
            raise RuntimeError(f"人工确认记录取值不合法: {item}")
        if community_id in seen_ids:
            raise RuntimeError(f"人工确认文件存在重复 id: {community_id}")
        seen_ids.add(community_id)
        values.append((community_id, build_year))
    return values


def append_results(path: Path, values: list[tuple[int, int]]) -> None:
    """追加用户确认来源，供后续回写 Excel 与审计。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    checked_at = _now()
    with path.open("a", encoding="utf-8") as output:
        for community_id, build_year in values:
            output.write(
                json.dumps(
                    {
                        "community_id": community_id,
                        "status": "SUCCESS",
                        "build_year": build_year,
                        "rule": "MANUAL_FANG_ANJUKE_CONFIRMED",
                        "evidence": SOURCE_EVIDENCE,
                        "source_name": SOURCE_NAME,
                        "source_urls": [],
                        "checked_at": checked_at,
                    },
                    ensure_ascii=False,
                )
            )
            output.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="写入人工确认的建成年份")
    parser.add_argument("--confirmations", type=Path, required=True, help="表格导出的确认 JSON")
    parser.add_argument("--database", type=Path, help="SQLite 路径")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH, help="人工确认结果 JSONL")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    values = load_confirmations(args.confirmations)
    if not values:
        print("没有已填写的 build_year，未写入 SQLite")
        return

    database = CommunityDatabase(args.database)
    for community_id, build_year in values:
        database.update_build_year(community_id, build_year)
    append_results(args.results, values)
    print(f"已写入人工确认建成年份: {len(values)} 条")
    print(f"结果日志: {args.results}")


if __name__ == "__main__":
    main()
