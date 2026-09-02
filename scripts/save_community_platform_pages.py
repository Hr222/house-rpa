# -*- coding: utf-8 -*-
"""编排层落库：把 MVP 产出的平台地址写入房源记录库。

职责链条：请求清单 > 主数据映射 community_id > RPA(MVP) 提供 URL > 本脚本存储。
读取 MVP 结果 JSON 与初始化清单映射，经 PropertyRecordsIngestion 写入
community_platform_pages；MVP 失败项与清单外名称只报告不写库。
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
        default="test_data/初始化清单_150.json",
        help="初始化清单映射 JSON（name -> community_ids）",
    )
    parser.add_argument("--seen-at", default=None, help="采集时间 ISO 字符串，缺省用当前时间")
    args = parser.parse_args()

    platform = PLATFORM_ALIASES.get(args.platform.strip().lower())
    if platform is None:
        print(f"不支持的平台: {args.platform}，可选：{sorted(set(PLATFORM_ALIASES.values()))}")
        return 1

    mapping_path = PROJECT_ROOT / args.mapping if not Path(args.mapping).is_absolute() else Path(args.mapping)
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    name_to_info = {item["name"]: item for item in mapping.get("resolved", [])}

    ingestion = PropertyRecordsIngestion()
    written = 0
    failed_results: list[str] = []
    no_mapping: list[str] = []
    errors: list[str] = []

    for raw_path in args.result_file:
        result_path = Path(raw_path)
        results = json.loads(result_path.read_text(encoding="utf-8"))
        log.info("读取结果: %s (%d 条)", result_path, len(results))
        for item in results:
            name = item.get("community_name", "")
            if not item.get("success"):
                failed_results.append(name)
                continue
            # 条目自带 community_ids/mapped_community_ids/city/district 时优先
            # （人工核对补充等场景），否则回退到清单映射。
            info = name_to_info.get(name) or {}
            ids = (
                item.get("community_ids")
                or item.get("mapped_community_ids")
                or info.get("community_ids")
                or []
            )
            city = item.get("city") or info.get("city")
            district = item.get("administrative_district") or info.get("district")
            if not ids or not city or not district:
                no_mapping.append(name)
                log.warning("[跳过] 清单无唯一 community_id: %s（跨组或未映射，需人工）", name)
                continue
            for community_id in ids:
                try:
                    ingestion.ingest_platform_result(
                        community_id=community_id,
                        city=city,
                        administrative_district=district,
                        source_platform=platform,
                        source_community_name=item.get("matched_search_name") or name,
                        listing_page_url=item["listing_page_url"],
                        seen_at=args.seen_at,
                    )
                    written += 1
                except Exception as exc:
                    errors.append(f"{name} community_id={community_id}: {exc}")
                    log.error("写入失败: %s community_id=%s → %s", name, community_id, exc)

    print("\n===== 落库汇总 =====")
    print(f"平台：{platform}")
    print(f"写入 community_platform_pages：{written} 行")
    print(f"MVP 失败项（未落库）：{len(failed_results)} → {failed_results}")
    print(f"清单外/跨组（未落库，需人工）：{len(no_mapping)} → {no_mapping}")
    print(f"写入异常：{len(errors)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
