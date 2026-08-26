# -*- coding: utf-8 -*-
"""撤销仅由 Q 房网自动写入的建成年份，等待房天下/安居客重新核验。"""

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
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "build_year_backfill.jsonl"
)


def load_qfang_success_ids(path: Path) -> set[int]:
    """按每个主键的最后一个 Q 房网结果识别需要撤销的自动写入。"""
    latest: dict[int, dict] = {}
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            try:
                item = json.loads(line)
                latest[int(item["community_id"])] = item
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
    return {
        community_id
        for community_id, item in latest.items()
        if item.get("status") == "SUCCESS"
        and str(item.get("rule") or "").startswith("QFANG_")
    }


def reset_build_years(database: CommunityDatabase, community_ids: set[int]) -> int:
    """只清空已被 Q 房网自动写入的值，不触碰人工确认或旧来源数据。"""
    if not community_ids:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ids = tuple(sorted(community_ids))
    placeholders = ",".join("?" for _ in ids)
    with database._connect() as connection:  # 受控的一次性来源迁移
        cursor = connection.execute(
            f"""
            UPDATE communities
            SET build_year = NULL, updated_at = ?
            WHERE community_id IN ({placeholders})
            """,
            (now, *ids),
        )
    return int(cursor.rowcount)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="撤销 Q 房网自动建成年份")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH, help="Q 房网结果日志")
    parser.add_argument("--database", type=Path, help="SQLite 路径")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    database = CommunityDatabase(args.database)
    qfang_ids = load_qfang_success_ids(args.results)
    updated = reset_build_years(database, qfang_ids)
    print(f"已撤销 Q 房网自动建成年份: {updated} 条")


if __name__ == "__main__":
    main()
