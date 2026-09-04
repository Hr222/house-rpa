# -*- coding: utf-8 -*-
"""一次性回填：为已初始化的链家/房天下入口批量补全成交页地址（deal_page_url）。

依据（2026-09-02 人工核对通过）的同尾段推导规则：
  链家   /ershoufang/c{id} 或组页 /ershoufang/sq{id} -> /chengjiao/{同尾段}/
  房天下 /house-xm{id}                               -> /loupan/{id}/chengjiao/
只处理 deal_page_url 为空的行（幂等，已有成交地址不覆盖）；挂牌 URL 不含
预期 ID 的行与主数据缺 ID 的行只报告不写库。城市/行政区从小区主数据按
community_id 关联。写入经既有 PropertyRecordsIngestion。

用法：
  python -m scripts.initialize_community_page.backfill_deal_pages [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.property_records.ingestion import PropertyRecordsIngestion


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

PAGES_DB = PROJECT_ROOT / "persist" / "property_records.sqlite3"
MASTER_DB = PROJECT_ROOT / "persist" / "community_data.sqlite3"

# 推导规则表：平台 -> (挂牌路径匹配, 成交页地址模板)
# 链家挂牌尾段两种形态都映射到 /chengjiao/{同尾段}/：小区页 c{id}、组页 sq{id}
# （sq6162 组页成交地址 2026-09-02 人工核对）。
DERIVE_RULES = {
    "lj": (re.compile(r"/ershoufang/([a-z]+\d+)"), "{origin}/chengjiao/{id}/"),
    "fang": (re.compile(r"/house-xm(\d+)"), "{origin}/loupan/{id}/chengjiao/"),
}


def build_deal_page_url(platform: str, listing_page_url: str) -> str | None:
    """按平台规则由挂牌页 URL 推导成交页 URL；不匹配返回 None。"""
    rule = DERIVE_RULES.get(platform)
    if rule is None:
        return None
    pattern, template = rule
    parsed = urlparse(listing_page_url or "")
    match = pattern.search(parsed.path)
    if match is None:
        return None
    return template.format(origin=f"{parsed.scheme}://{parsed.hostname}", id=match.group(1))


def load_backfill_rows() -> tuple[list[dict], list[dict], list[dict]]:
    """只读关联两库，返回（可回填行、无法推导行、主数据缺 ID 行）。"""
    pages = sqlite3.connect(f"file:{PAGES_DB}?mode=ro", uri=True)
    pages.row_factory = sqlite3.Row
    rows = pages.execute(
        "SELECT community_id, source_platform, source_community_name, "
        "listing_page_url, deal_page_url FROM community_platform_pages "
        "WHERE deal_page_url IS NULL OR deal_page_url = ''"
    ).fetchall()
    pages.close()

    master = sqlite3.connect(f"file:{MASTER_DB}?mode=ro", uri=True)
    master.row_factory = sqlite3.Row
    communities = {
        r["community_id"]: r
        for r in master.execute("SELECT community_id, city, administrative_district FROM communities")
    }
    master.close()

    backfill: list[dict] = []
    unmatched: list[dict] = []
    no_master: list[dict] = []
    for row in rows:
        platform = row["source_platform"]
        if platform not in DERIVE_RULES:
            continue  # 无成交页的平台（ke/ajk/lyj 等），不在回填范围
        derived = build_deal_page_url(platform, row["listing_page_url"])
        info = communities.get(row["community_id"])
        if derived is None:
            unmatched.append({"row": dict(row), "reason": "挂牌 URL 不含预期 ID"})
            continue
        if info is None:
            no_master.append({"row": dict(row), "reason": f"主数据无 community_id={row['community_id']}"})
            continue
        backfill.append(
            {
                "community_id": row["community_id"],
                "city": info["city"],
                "administrative_district": info["administrative_district"],
                "source_platform": platform,
                "source_community_name": row["source_community_name"],
                "listing_page_url": row["listing_page_url"],
                "deal_page_url": derived,
            }
        )
    return backfill, unmatched, no_master


def main() -> int:
    parser = argparse.ArgumentParser(description="批量回填 lj/fang 成交页地址（幂等，只填空行）")
    parser.add_argument("--dry-run", action="store_true", help="只预览将回填的行，不写库")
    args = parser.parse_args()

    backfill, unmatched, no_master = load_backfill_rows()
    log.info("可回填 %d 行；无法推导 %d 行；主数据缺 ID %d 行", len(backfill), len(unmatched), len(no_master))

    report = {"written": 0, "errors": []}
    ingestion = None if args.dry_run else PropertyRecordsIngestion()
    for item in backfill:
        if args.dry_run:
            report["written"] += 1
            log.info(
                "[dry-run] 将写入 %s(%s) 平台=%s deal=%s",
                item["source_community_name"],
                item["community_id"],
                item["source_platform"],
                item["deal_page_url"],
            )
            continue
        try:
            ingestion.ingest_platform_result(**item)
            report["written"] += 1
        except Exception as exc:
            log.error("写入失败：%s(%s) 平台=%s：%s", item["source_community_name"], item["community_id"], item["source_platform"], exc)
            report["errors"].append(f"{item['source_community_name']} community_id={item['community_id']}: {exc}")

    mode = "dry-run 预览" if args.dry_run else "实际写入"
    print(f"\n===== 回填汇总（{mode}）=====")
    print(f"写入 deal_page_url：{report['written']} 行")
    print(f"无法推导（需人工）：{len(unmatched)} 行")
    for item in unmatched:
        print(f"  - [{item['row']['source_platform']}] {item['row']['source_community_name']}: {item['row']['listing_page_url']}")
    print(f"主数据缺 ID（需人工）：{len(no_master)} 行")
    print(f"写入异常：{len(report['errors'])}")
    for error in report["errors"]:
        print(f"  - {error}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
