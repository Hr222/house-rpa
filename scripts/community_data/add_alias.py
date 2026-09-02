# -*- coding: utf-8 -*-
"""为小区记录追加人工确认的别名，按 community_id 幂等写入。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import CommunityDatabase


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为小区记录追加别名（幂等）")
    parser.add_argument(
        "--id",
        action="append",
        required=True,
        type=int,
        help="community_id，可重复传入多个记录",
    )
    parser.add_argument("--alias", required=True, help="要追加的别名")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = CommunityDatabase()
    for community_id in args.id:
        record = database.get_by_id(community_id)
        if record is None:
            print(f"[missing] id={community_id} 记录不存在")
            return 1
        added = database.add_alias(community_id, args.alias)
        state = "added" if added else "skipped"
        print(f"[{state}] id={community_id} name={record.name} alias={args.alias}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
