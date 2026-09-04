# -*- coding: utf-8 -*-
"""小区平台入口统一初始化指令入口（挂牌页全平台；成交页仅链家/房天下）。

以人工维护的清单 JSON 为唯一指令源，批量更新房源记录库的
``community_platform_pages``：给定小区清单（小区名/行政区/片区/别名/
community_id），逐平台复用各 ``*_community_page_mvp`` 的核心流程
（搜索 → 严格核对行政区/名称归属 → 产出入口 URL），汇总后经
``PropertyRecordsIngestion`` 幂等写入。成交页（deal_page_url）只有
链家与房天下有。

清单 JSON 格式（城市当前仅深圳，格式保留城市字段便于后续扩展）：
{
    "city": "深圳",
    "communities": [
        {
            "community_name": "联城美园",
            "administrative_district": "罗湖区",
            "community_district": "春风路",
            "aliases": ["美园"],
            "community_id": 3
        }
    ]
}

community_id 必填：落库归属以清单为准。数据库更新统一对接已验证的
save_community_platform_pages.py（同目录），本入口不直接依赖 app 落库能力。
aliases 为主数据核对信息，搜索兜底仍由各 MVP 内部查主数据（同一来源）。

用法：
  python -m scripts.initialize_community_page.init_community_pages \
      --input test_data/community_init_list.json \
      [--platforms ajk ke fang lj lyj] [--manual-login] [--dry-run] [--debug]

边界：本脚本是运行入口壳，不做页面解析；浏览器、登录态与风控协议全部
留在各 MVP 模块内（独立浏览器、固定 profile、拦截时人工回车确认）。
主数据只读；本入口不直接依赖 app 落库能力，数据库更新统一经同目录的
save_community_platform_pages.py 对接。
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
from pathlib import Path
import sys
import time

import nodriver as uc

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.initialize_community_page.save_community_platform_pages import ingest_platform_results


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

PLATFORM_MODULES = {
    "ajk": "scripts.initialize_community_page.community_page.ajk_community_page_mvp",
    "ke": "scripts.initialize_community_page.community_page.ke_community_page_mvp",
    "fang": "scripts.initialize_community_page.community_page.fang_community_page_mvp",
    "lj": "scripts.initialize_community_page.community_page.lj_community_page_mvp",
    "lyj": "scripts.initialize_community_page.community_page.lyj_community_page_mvp",
}
# 成交页只有链家与房天下；房天下主流程无片区辅助参数。
SUPPORTS_DEAL = {"lj", "fang"}
SUPPORTS_COMMUNITY_DISTRICT = {"ajk", "ke", "lj", "lyj"}


def load_instruction_list(path: Path) -> dict:
    """读取并校验清单 JSON，返回 {"city": str, "entries": [规整后的清单项]}。

    必填字段：community_name、administrative_district、community_id（落库
    归属以清单为准）；可选：community_district、aliases。（行政区, 小区名）
    重复视为清单错误，避免结果回填歧义。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"清单文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"清单不是合法 JSON：{path}（{exc}）") from exc

    city = str(raw.get("city") or "").strip()
    if not city:
        raise ValueError("清单缺少顶层 city（如 \"深圳\"）")
    communities = raw.get("communities")
    if not isinstance(communities, list) or not communities:
        raise ValueError("清单缺少非空 communities 数组")

    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(communities):
        name = str(item.get("community_name") or "").strip()
        district = str(item.get("administrative_district") or "").strip()
        if not name or not district:
            raise ValueError(f"清单第 {index + 1} 项缺少 community_name 或 administrative_district")
        if (district, name) in seen:
            raise ValueError(f"清单存在重复项：行政区={district} 小区={name}")
        seen.add((district, name))

        community_id = item.get("community_id")
        if isinstance(community_id, str) and community_id.strip().isdigit():
            community_id = int(community_id.strip())
        if not isinstance(community_id, int):
            raise ValueError(
                f"清单第 {index + 1} 项 community_id 必填且为整数：{community_id!r}"
            )

        aliases_raw = item.get("aliases") or []
        if not isinstance(aliases_raw, list):
            raise ValueError(f"清单第 {index + 1} 项 aliases 必须是数组")
        entries.append(
            {
                "community_name": name,
                "administrative_district": district,
                "community_district": str(item.get("community_district") or "").strip(),
                "aliases": [str(alias).strip() for alias in aliases_raw if str(alias).strip()],
                "community_id": community_id,
            }
        )
    return {"city": city, "entries": entries}


def group_by_district(entries: list[dict]) -> dict[str, list[dict]]:
    """清单项按行政区分组，保持出现顺序。"""
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(entry["administrative_district"], []).append(entry)
    return groups


async def run_platforms(instruction: dict, args: argparse.Namespace) -> tuple[list[dict], list[dict]]:
    """按“平台 × 行政区”顺序执行初始化，返回全部小区结果与平台级中断记录。

    MVP 批次按行政区运行（同名小区跨区不混淆）；片区/别名/community_id 属于
    清单项信息，执行完由本层回填到结果，不作为 MVP 批次参数。各平台 main
    独立开浏览器并自动关闭（manual_close=False）；单小区失败不断批，
    单平台中断记录后继续下一平台。
    """
    results: list[dict] = []
    platform_errors: list[dict] = []
    groups = group_by_district(instruction["entries"])
    for platform in args.platforms:
        module = importlib.import_module(PLATFORM_MODULES[platform])
        for district, entries in groups.items():
            kwargs: dict = {
                "city": instruction["city"],
                "administrative_district": district,
                "community_names": [entry["community_name"] for entry in entries],
                "manual_login": args.manual_login,
                "debug": args.debug,
                "manual_close": False,
            }
            if platform in SUPPORTS_COMMUNITY_DISTRICT:
                kwargs["community_district"] = ""
            if platform in SUPPORTS_DEAL:
                kwargs["include_deal"] = True

            log.info(
                "===== 平台[%s] 行政区[%s] 开始入口初始化（共 %d 个小区）=====",
                platform,
                district,
                len(entries),
            )
            try:
                summaries = await module.main(**kwargs)
            except Exception as exc:
                log.error("平台[%s] 行政区[%s] 批量流程异常中断，继续下一组：%s", platform, district, exc)
                platform_errors.append(
                    {"platform": platform, "administrative_district": district, "error": str(exc)}
                )
                continue

            entry_by_name = {entry["community_name"]: entry for entry in entries}
            for item in summaries:
                item["platform"] = platform
                entry = entry_by_name.get(item.get("community_name", ""))
                if entry is not None:
                    item["administrative_district"] = entry["administrative_district"]
                    item["community_district"] = entry["community_district"]
                    item["community_id"] = entry["community_id"]
            results.extend(summaries)
            success = sum(1 for item in summaries if item.get("success"))
            log.info("平台[%s] 行政区[%s] 完成：成功 %d / 共 %d", platform, district, success, len(summaries))
    return results, platform_errors


def update_database(results: list[dict], dry_run: bool) -> dict:
    """落库：按平台分组交给 save_community_platform_pages 批量写入。

    本入口不直接依赖 app 落库能力；清单每项必带 community_id，归属以清单
    为准。采集失败项在本层报告，不进入落库；dry-run 只做预览计数。
    """
    report: dict = {"dry_run": dry_run, "written": 0, "no_mapping": [], "errors": [], "skipped": []}
    success_items: list[dict] = []
    for item in results:
        if not item.get("success") or not item.get("listing_page_url"):
            report["skipped"].append(
                {
                    "community_name": item.get("community_name", ""),
                    "platform": item.get("platform", ""),
                    "reason": item.get("error") or "缺少 listing_page_url",
                }
            )
            continue
        if dry_run:
            report["written"] += 1
            log.info(
                "[dry-run] 将写入 %s(%s) 平台=%s listing=%s deal=%s",
                item.get("community_name"),
                item.get("community_id"),
                item.get("platform"),
                item["listing_page_url"],
                item.get("deal_page_url") or "无",
            )
            continue
        success_items.append(item)

    by_platform: dict[str, list[dict]] = {}
    for item in success_items:
        by_platform.setdefault(item["platform"], []).append(item)
    for platform, items in by_platform.items():
        platform_report = ingest_platform_results(platform, items)
        report["written"] += platform_report["written"]
        for name in platform_report["no_mapping"]:
            report["no_mapping"].append({"community_name": name, "platform": platform})
        for error in platform_report["errors"]:
            report["errors"].append(
                {"community_name": error.split(" community_id=", 1)[0], "platform": platform, "error": error}
            )
    return report


def write_result_file(path: Path, payload: dict) -> None:
    """把统一结果与落库报告写为 JSON 台账。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写 {path}")


def print_report(results: list[dict], platform_errors: list[dict], report: dict) -> None:
    """打印与 MVP/save 脚本同风格的文本汇总。"""
    print("\n===== 批量结果汇总 =====")
    for item in results:
        location = f"[{item.get('platform')}] {item.get('administrative_district', '')}"
        if item.get("success"):
            deal = item.get("deal_page_url") or ("失败" if item.get("deal_error") else "不适用")
            print(
                f"[成功] {location} {item.get('community_name')}："
                f"listing={item.get('listing_page_url')}，deal={deal}"
            )
        else:
            print(f"[失败] {location} {item.get('community_name')}：{item.get('error')}")
    for item in platform_errors:
        print(f"[平台中断] [{item['platform']}] {item['administrative_district']}：{item['error']}")

    print("\n===== 落库汇总 =====")
    mode = "dry-run 预览" if report["dry_run"] else "实际写入"
    print(f"模式：{mode}；写入 {report['written']} 条")
    for item in report["no_mapping"]:
        print(f"[需人工·无归属] [{item['platform']}] {item['community_name']}：清单未提供唯一 community_id")
    for item in report["errors"]:
        print(f"[落库失败] [{item['platform']}] {item['error']}")
    for item in report["skipped"]:
        print(f"[采集失败] [{item['platform']}] {item['community_name']}：{item['reason']}")


def main() -> int:
    """解析指令并串起“加载清单 → 逐平台初始化 → 落库 → 汇总”。"""
    parser = argparse.ArgumentParser(
        description="小区平台入口统一初始化（挂牌页全平台；成交页仅 lj/fang）；清单 JSON 为指令源"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="人工维护的清单 JSON 路径（相对路径按项目根解析），格式见文件头 docstring",
    )
    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=sorted(PLATFORM_MODULES),
        default=sorted(PLATFORM_MODULES),
        help="平台代码，缺省全部 5 平台",
    )
    parser.add_argument("--manual-login", action="store_true", help="验证码/登录拦截时置前浏览器等待人工处理")
    parser.add_argument("--debug", action="store_true", help="各平台导出关键页面 HTML")
    parser.add_argument("--dry-run", action="store_true", help="只采集与映射预览，不写 community_platform_pages")
    parser.add_argument(
        "--result-file",
        default=None,
        help="统一结果 JSON 路径；缺省 results/community_page/init_community_pages_<时间戳>.json",
    )
    args = parser.parse_args()

    input_path = Path(args.input) if Path(args.input).is_absolute() else PROJECT_ROOT / args.input
    try:
        instruction = load_instruction_list(input_path)
    except ValueError as exc:
        print(f"[清单错误] {exc}")
        return 1
    groups = group_by_district(instruction["entries"])
    log.info(
        "清单加载：城市=%s，小区 %d 个，行政区 %d 个",
        instruction["city"],
        len(instruction["entries"]),
        len(groups),
    )

    results, platform_errors = uc.loop().run_until_complete(run_platforms(instruction, args))
    report = update_database(results, args.dry_run)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input_file": str(input_path),
        "city": instruction["city"],
        "platforms": args.platforms,
        "dry_run": args.dry_run,
        "results": results,
        "platform_errors": platform_errors,
        "ingest_report": report,
    }
    result_path = Path(args.result_file) if args.result_file else (
        PROJECT_ROOT / "results" / "community_page" / f"init_community_pages_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    write_result_file(result_path, payload)
    print_report(results, platform_errors, report)
    log.info(
        "统一初始化完成：小区结果 %d 条，写入 %d 条，需人工 %d 条，平台中断 %d 个",
        len(results),
        report["written"],
        len(report["no_mapping"]),
        len(platform_errors),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
