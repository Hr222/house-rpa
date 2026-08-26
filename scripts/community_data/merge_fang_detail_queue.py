# -*- coding: utf-8 -*-
"""按详情 URL 合并房天下候选队列，保留全部待核验小区。"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any


def unique_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一详情页的每个主小区只保留一次候选记录。"""
    seen: set[int] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        for candidate in item.get("candidate_records", []):
            try:
                community_id = int(candidate["community_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if community_id not in seen:
                seen.add(community_id)
                output.append(candidate)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="按 URL 合并房天下详情候选队列")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only-duplicates", action="store_true")
    args = parser.parse_args()

    queue = json.loads(args.input.read_text(encoding="utf-8"))
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in queue:
        detail_url = str(item.get("detail_url") or "")
        if detail_url:
            grouped.setdefault(detail_url, []).append(item)

    output: list[dict[str, Any]] = []
    for detail_url, items in grouped.items():
        if args.only_duplicates and len(items) < 2:
            continue
        candidates = unique_candidates(items)
        if not candidates:
            continue
        source_names = [str(item.get("source_name") or "") for item in items]
        discovery_candidates = [
            candidate
            for item in items
            for candidate in item.get("discovery_candidates", [])
        ]
        output.append(
            {
                "source_name": source_names[0],
                "source_names": source_names,
                "detail_url": detail_url,
                "candidate_records": candidates,
                "discovery_candidates": discovery_candidates,
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "input": len(queue),
                "unique_urls": len(grouped),
                "output": len(output),
                "candidate_records": sum(len(item["candidate_records"]) for item in output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
