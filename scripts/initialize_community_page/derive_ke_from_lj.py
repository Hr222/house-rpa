# -*- coding: utf-8 -*-
"""从 lj 入口按同 id 直接派生 ke 入口（贝壳与链家小区 communityId 一致）。

背景：贝壳与链家同属一个房源库，小区 communityId 完全一致（库内双平台小区
实测 id 逐一相同）。ke 平台因此不需要独立初始化：lj 初始化完成后，本脚本把
每个已具备 lj 挂牌入口的小区按同 id 派生贝壳挂牌入口（/ershoufang/c<id>，
无尾斜杠，与库内既有 ke 数据同构）。贝壳不落成交入口（与库内既有口径一致）。

只补缺失：已有 ke 挂牌入口（含历史特殊格式如 sq6162）的小区一律跳过不覆盖；
lj URL 解析不出 c<id> 的小区跳过并告警。写入复用 PropertyRecordsIngestion
（upsert、主数据归属校验）。

用法：
  python -m scripts.initialize_community_page.derive_ke_from_lj               # 全库扫
  python -m scripts.initialize_community_page.derive_ke_from_lj --input <清单.json>
  python -m scripts.initialize_community_page.derive_ke_from_lj --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from app.community_data.database import CommunityDatabase
from app.property_records.ingestion import PropertyRecordsIngestion
from app.rpa.platforms.city_map import get_start_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "persist" / "property_records.sqlite3"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)


def load_input_ids(path: Path) -> set[int]:
    """读取清单 JSON，返回其中的 community_id 集合（格式同统一初始化入口）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    ids: set[int] = set()
    for item in raw.get("communities") or []:
        community_id = item.get("community_id")
        if isinstance(community_id, int):
            ids.add(community_id)
    return ids


def main() -> int:
    """扫库派生：lj 有挂牌入口且 ke 缺失的小区，按同 id 写入贝壳挂牌入口。"""
    parser = argparse.ArgumentParser(description="从 lj 入口按同 id 派生 ke 入口")
    parser.add_argument("--input", default=None, help="可选：清单 JSON 路径，仅派生其中的小区")
    parser.add_argument("--dry-run", action="store_true", help="只预览派生结果，不写库")
    args = parser.parse_args()

    filter_ids = load_input_ids(Path(args.input)) if args.input else None

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    lj_rows = connection.execute(
        "SELECT community_id, source_community_name, listing_page_url "
        "FROM community_platform_pages "
        "WHERE source_platform='lj' AND listing_page_url IS NOT NULL AND listing_page_url != ''"
    ).fetchall()
    ke_existing = {
        row[0]
        for row in connection.execute(
            "SELECT community_id FROM community_platform_pages "
            "WHERE source_platform='ke' AND listing_page_url IS NOT NULL AND listing_page_url != ''"
        )
    }
    connection.close()

    master = CommunityDatabase()
    ingestion = PropertyRecordsIngestion()
    ke_start = urlparse(get_start_url("ke", "深圳"))
    ke_origin = f"{ke_start.scheme}://{(ke_start.hostname or '').lower()}"

    derived = skipped_ke = skipped_url = skipped_master = 0
    errors: list[str] = []
    for community_id, source_name, lj_url in lj_rows:
        if filter_ids is not None and community_id not in filter_ids:
            continue
        if community_id in ke_existing:
            skipped_ke += 1
            continue
        match = re.search(r"/ershoufang/c(\d+)/?$", lj_url)
        if not match:
            skipped_url += 1
            log.warning("lj URL 解析不出小区 id，跳过：community_id=%s %s", community_id, lj_url)
            continue
        record = master.get_by_id(community_id)
        if record is None:
            skipped_master += 1
            log.warning("主数据无此小区，跳过：community_id=%s", community_id)
            continue
        listing_page_url = f"{ke_origin}/ershoufang/c{match.group(1)}"
        if args.dry_run:
            derived += 1
            log.info("[dry-run] 将派生 ke：%s -> %s", community_id, listing_page_url)
            continue
        try:
            ingestion.ingest_platform_result(
                community_id=community_id,
                city=record.city,
                administrative_district=record.administrative_district,
                source_platform="ke",
                source_community_name=source_name,
                listing_page_url=listing_page_url,
            )
            derived += 1
        except Exception as exc:
            errors.append(f"community_id={community_id}: {exc}")
            log.error("派生写入失败：community_id=%s %s", community_id, exc)

    print(
        f"派生 ke {derived} 条（dry-run={args.dry_run}）；"
        f"已有 ke 跳过 {skipped_ke}；lj URL 无法解析 {skipped_url}；"
        f"主数据缺失 {skipped_master}；写失败 {len(errors)}"
    )
    for error in errors:
        print(f"[派生失败] {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
