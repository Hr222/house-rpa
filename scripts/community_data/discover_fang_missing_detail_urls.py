# -*- coding: utf-8 -*-
"""从已抓取的房天下小区索引中发现剩余小区的候选详情 URL。

候选只用于后续详情页校验；本脚本不会直接写入建成年份，最终仍由
retry_fang_missing_build_year.py 严格校验行政区和主名称后回填。
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_community_index.json"
DEFAULT_QUEUE = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_detail_discovery_queue_20260826.json"
DEFAULT_DATABASE = PROJECT_ROOT / "persist" / "community_data.sqlite3"
DEFAULT_RETRY_RAW = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_detail_retry_results_20260825.jsonl"


def normalize(value: Any) -> str:
    return re.sub(r"[\s()（）\[\]【】·•,，。]", "", str(value or "")).casefold()


def grams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def load_seen_urls(path: Path) -> set[str]:
    seen: set[str] = set()
    if not path.exists():
        return seen
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("detail_url"):
            seen.add(str(item["detail_url"]))
    return seen


def candidate_urls(query_names: list[str], index: list[dict[str, Any]], gram_index: dict[str, set[int]], limit: int) -> list[dict[str, Any]]:
    scored: dict[str, tuple[float, str]] = {}
    for query in query_names:
        normalized = normalize(query)
        if not normalized:
            continue
        possible: set[int] = set()
        for gram in grams(normalized):
            possible.update(gram_index.get(gram, set()))
        for index_id in possible:
            entry = index[index_id]
            indexed_name = normalize(entry.get("name"))
            if not indexed_name:
                continue
            if normalized == indexed_name:
                score = 1.0
            elif len(normalized) >= 4 and (normalized in indexed_name or indexed_name in normalized):
                score = 0.92
            else:
                score = difflib.SequenceMatcher(None, normalized, indexed_name).ratio()
            if score < 0.62:
                continue
            url = str(entry.get("detail_url") or "")
            if not url:
                continue
            prior = scored.get(url)
            if prior is None or score > prior[0]:
                scored[url] = (score, str(entry.get("name") or ""))
    ranked = sorted(
        (
            {"detail_url": url, "index_name": name, "score": round(score, 4)}
            for url, (score, name) in scored.items()
        ),
        key=lambda item: (-item["score"], item["index_name"], item["detail_url"]),
    )
    return ranked[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="发现剩余深圳小区的房天下候选详情页")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--retry-raw", type=Path, default=DEFAULT_RETRY_RAW)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--per-community", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.62)
    args = parser.parse_args()

    payload = json.loads(args.index.read_text(encoding="utf-8"))
    index = [entry for entry in payload.get("entries", []) if entry.get("name") and entry.get("detail_url")]
    gram_index: dict[str, set[int]] = defaultdict(set)
    for index_id, entry in enumerate(index):
        for gram in grams(normalize(entry["name"])):
            gram_index[gram].add(index_id)

    conn = sqlite3.connect(args.database)
    rows = conn.execute(
        "select community_id, administrative_district, district, name, aliases_json "
        "from communities where build_year is null order by community_id"
    ).fetchall()
    seen_urls = load_seen_urls(args.retry_raw)
    output: list[dict[str, Any]] = []
    no_candidates = 0
    for community_id, administrative_district, district, name, aliases_json in rows:
        aliases = json.loads(aliases_json or "[]")
        names = [str(name), *(str(alias) for alias in aliases)]
        candidates = [
            item
            for item in candidate_urls(names, index, gram_index, args.per_community)
            if item["score"] >= args.min_score and item["detail_url"] not in seen_urls
        ]
        if not candidates:
            no_candidates += 1
            continue
        candidate_record = {
            "community_id": community_id,
            "administrative_district": administrative_district,
            "district": district,
            "name": name,
            "aliases": aliases,
        }
        for candidate in candidates:
            output.append(
                {
                    "source_name": name,
                    "detail_url": candidate["detail_url"],
                    "candidate_records": [candidate_record],
                    "discovery_candidates": candidates,
                }
            )
    args.queue.parent.mkdir(parents=True, exist_ok=True)
    args.queue.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"communities_missing": len(rows), "queue": len(output), "no_candidates": no_candidates}, ensure_ascii=False))


if __name__ == "__main__":
    main()
