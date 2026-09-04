# -*- coding: utf-8 -*-
"""编排层落库：把 MVP/统一入口产出的平台地址写入房源记录库。

职责链条：请求清单 > 主数据映射 community_id > RPA(MVP) 提供 URL > 本脚本存储。
读取 MVP 结果 JSON（或统一入口 init_community_pages 的结果项）与可选的初始化
清单映射，经 PropertyRecordsIngestion 写入 community_platform_pages；失败项与
无归属项只报告不写库。

结果项字段：community_name、success、city、administrative_district、
listing_page_url，可选 matched_search_name、deal_page_url（lj/fang 成交入口）。
归属优先级：community_ids > mapped_community_ids > 单数 community_id
（统一入口清单自带）> 清单映射 --mapping。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.property_records.ingestion import PropertyRecordsIngestion


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

PLATFORM_ALIASES = {
    "fang": "fang", "房天下": "fang",
    "ajk": "ajk", "安居客": "ajk",
    "lyj": "lyj", "乐有家": "lyj",
    "ke": "ke", "贝壳": "ke",
    "lj": "lj", "链家": "lj",
}


def normalize_platform(raw: str) -> str | None:
    """平台别名归一化：fang/房天下 -> fang 等；未支持返回 None。"""
    return PLATFORM_ALIASES.get(raw.strip().lower())


def ingest_platform_results(
    platform: str,
    results: list[dict],
    mapping: dict | None = None,
    seen_at: str | None = None,
) -> dict:
    """把一批结果项落库到 community_platform_pages，返回落库报告。

    归属优先级：community_ids > mapped_community_ids > 单数 community_id
    （统一入口清单自带）> 清单映射 mapping；缺失归属、城市或行政区的条目
    只报告不写库。deal_page_url 存在时一并写入同一入口行。
    """
    canonical = normalize_platform(platform)
    if canonical is None:
        raise ValueError(f"不支持的平台: {platform}，可选：{sorted(set(PLATFORM_ALIASES.values()))}")
    name_to_info = {item["name"]: item for item in (mapping or {}).get("resolved", [])}

    ingestion = PropertyRecordsIngestion()
    report: dict = {
        "platform": canonical,
        "written": 0,
        "failed_results": [],
        "no_mapping": [],
        "errors": [],
    }
    for item in results:
        name = item.get("community_name", "")
        if not item.get("success"):
            report["failed_results"].append(name)
            continue
        info = name_to_info.get(name) or {}
        ids = (
            item.get("community_ids")
            or item.get("mapped_community_ids")
            or ([item["community_id"]] if item.get("community_id") is not None else [])
            or info.get("community_ids")
            or []
        )
        city = item.get("city") or info.get("city")
        district = item.get("administrative_district") or info.get("district")
        if not ids or not city or not district:
            report["no_mapping"].append(name)
            log.warning("[跳过] 无唯一 community_id: %s（跨组或未映射，需人工）", name)
            continue
        for community_id in ids:
            try:
                ingestion.ingest_platform_result(
                    community_id=community_id,
                    city=city,
                    administrative_district=district,
                    source_platform=canonical,
                    source_community_name=item.get("matched_search_name") or name,
                    listing_page_url=item["listing_page_url"],
                    deal_page_url=item.get("deal_page_url"),
                    seen_at=seen_at,
                )
                report["written"] += 1
            except Exception as exc:
                report["errors"].append(f"{name} community_id={community_id}: {exc}")
                log.error("写入失败: %s community_id=%s → %s", name, community_id, exc)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="把 MVP 平台地址结果落库到 community_platform_pages")
    parser.add_argument("--platform", required=True, help="平台：fang/ajk/lyj/ke/lj（或中文名）")
    parser.add_argument(
        "--result-file",
        required=True,
        action="append",
        help="MVP 结果 JSON 路径，可重复传入多个",
    )
    parser.add_argument(
        "--mapping",
        default=None,
        help="初始化清单映射 JSON（name -> community_ids）；结果项自带 community_id 时可不传",
    )
    parser.add_argument("--seen-at", default=None, help="采集时间 ISO 字符串，缺省用当前时间")
    args = parser.parse_args()

    platform = normalize_platform(args.platform)
    if platform is None:
        print(f"不支持的平台: {args.platform}，可选：{sorted(set(PLATFORM_ALIASES.values()))}")
        return 1

    mapping = None
    if args.mapping:
        mapping_path = PROJECT_ROOT / args.mapping if not Path(args.mapping).is_absolute() else Path(args.mapping)
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

    all_results: list[dict] = []
    for raw_path in args.result_file:
        result_path = Path(raw_path)
        results = json.loads(result_path.read_text(encoding="utf-8"))
        log.info("读取结果: %s (%d 条)", result_path, len(results))
        all_results.extend(results)

    report = ingest_platform_results(platform, all_results, mapping=mapping, seen_at=args.seen_at)

    print("\n===== 落库汇总 =====")
    print(f"平台：{report['platform']}")
    print(f"写入 community_platform_pages：{report['written']} 行")
    print(f"MVP 失败项（未落库）：{len(report['failed_results'])} → {report['failed_results']}")
    print(f"清单外/跨组（未落库，需人工）：{len(report['no_mapping'])} → {report['no_mapping']}")
    print(f"写入异常：{len(report['errors'])}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
