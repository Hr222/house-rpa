# -*- coding: utf-8 -*-
"""汇总房天下详情页结果，并按建成年份规则回填 SQLite。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import CommunityDatabase


DEFAULT_RAW_PATH = (
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_detail_results.jsonl"
)
DEFAULT_RESULTS_PATH = (
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_build_year_backfill.jsonl"
)
DEFAULT_MANUAL_PATH = (
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "build_year_manual_confirmations.jsonl"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_jsonl(path: Path) -> list[dict]:
    """读取容错 JSONL；中断写入的半行不会阻塞下一次汇总。"""
    if not path.exists():
        return []
    values: list[dict] = []
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                values.append(item)
    return values


def manual_confirmation_ids(path: Path) -> set[int]:
    """人工确认是最高优先级，后续来源不得覆盖。"""
    return {
        int(item["community_id"])
        for item in load_jsonl(path)
        if item.get("status") == "SUCCESS"
        and item.get("rule") == "MANUAL_FANG_ANJUKE_CONFIRMED"
        and str(item.get("community_id") or "").isdigit()
    }


def aggregate_fang_results(raw_results: list[dict], manual_ids: set[int]) -> list[dict]:
    """每个小区汇总多个房天下详情来源，执行一年差异规则。"""
    grouped: dict[int, list[dict]] = defaultdict(list)
    for item in raw_results:
        try:
            community_id = int(item["community_id"])
            build_year = int(item["build_year"])
        except (KeyError, TypeError, ValueError):
            continue
        if community_id in manual_ids or not 1800 <= build_year <= 2026:
            continue
        if item.get("status") != "SUCCESS":
            continue
        grouped[community_id].append(item)

    results: list[dict] = []
    for community_id, entries in sorted(grouped.items()):
        years = sorted({int(entry["build_year"]) for entry in entries})
        source_urls = sorted({str(entry.get("detail_url") or "") for entry in entries if entry.get("detail_url")})
        evidence = "；".join(
            sorted(
                {
                    f"房天下：{entry.get('detail_name') or entry.get('source_name') or '小区'} "
                    f"建筑年代 {int(entry['build_year'])}"
                    for entry in entries
                }
            )
        )
        result = {
            "community_id": community_id,
            "status": "SUCCESS",
            "build_year": None,
            "candidate_years": years,
            "rule": "FANG_SINGLE_OR_CONSISTENT",
            "evidence": evidence,
            "source_name": "房天下",
            "source_urls": source_urls,
            "checked_at": _now(),
        }
        if years[-1] - years[0] > 1:
            result["status"] = "MANUAL_REVIEW"
            result["rule"] = "FANG_REPORTED_YEAR_RANGE_OVER_1"
        else:
            result["build_year"] = years[-1]
            if len(years) > 1:
                result["rule"] = "FANG_LATEST_WITHIN_1_YEAR"
        results.append(result)
    return results


def write_results(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for result in results:
            output.write(json.dumps(result, ensure_ascii=False))
            output.write("\n")


def apply_successful_results(database: CommunityDatabase, results: list[dict]) -> int:
    """仅填充仍为空的年份，避免覆盖人工或既有可信来源。"""
    pending_ids = {record.community_id for record in database.list_pending_build_year()}
    updated = 0
    for result in results:
        if result["status"] != "SUCCESS" or result["community_id"] not in pending_ids:
            continue
        database.update_build_year(result["community_id"], int(result["build_year"]))
        updated += 1
    return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="汇总房天下建成年份回填结果")
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW_PATH, help="房天下详情原始 JSONL")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH, help="汇总后的 JSONL")
    parser.add_argument("--manual", type=Path, default=DEFAULT_MANUAL_PATH, help="人工确认 JSONL")
    parser.add_argument("--database", type=Path, help="SQLite 路径")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    results = aggregate_fang_results(load_jsonl(args.raw), manual_confirmation_ids(args.manual))
    write_results(args.results, results)
    updated = apply_successful_results(CommunityDatabase(args.database), results)
    summary = {
        "raw": str(args.raw),
        "results": str(args.results),
        "aggregated": len(results),
        "success": sum(item["status"] == "SUCCESS" for item in results),
        "manual_review": sum(item["status"] == "MANUAL_REVIEW" for item in results),
        "sqlite_updated": updated,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
