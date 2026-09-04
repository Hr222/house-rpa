# -*- coding: utf-8 -*-
"""小区数据抓取编排层（对齐询价编排的口径；结构与 initialize_community_page 对齐）。

收集链路（2026-09-03 改造，第一步：鉴定/包装/记录/算法 全部调用工程件）：
  指令源只接收 Excel（test_data 房产评估汇总表格式）或 JSON；
  鉴定：community 层 resolve_communities 确认身份（仅住宅、唯一命中，
  别名兜底由 community 层自带；未命中/非住宅/多期进"需人工"报告）；
  包装：确认小区构造工程 ConfirmedCommunityContext（与询价编排同款）；
  RPA：按平台调用整改成"URL 直达抓取形式"的 MVP（当前仅 ajk），
  风控/重试协议不动；
  记录：采集结果包装成工程 PlatformResult，经
  PropertyRecordsIngestion.ingest_rpa_result 入库。ajk 现阶段无成交
  明细、挂牌快照缺详情链接（listing_records 置空），实际入库物为入口
  行补全；详情链接方案确定后填充 listing_records 即可，零重构；
  算法：编排层调用工程 evaluate_algorithm（加权落点中位数），
  RPA 层只返回原始数据。

用法：
  python -m scripts.collect_community_data.collect_community_data \
      --input test_data/房产评估汇总表_新增行政区.xlsx \
      [--platforms ajk] [--dry-run] [--debug]
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
import sqlite3
import sys
import time

import nodriver as uc

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.algorithm.models import AlgorithmInput
from app.algorithm.weighted_median import evaluate_algorithm
from app.algorithm.config import get_weighted_median_discount
from app.community_data import resolve_communities
from app.community_data.models import EstateType
from app.inquiry.models import ConfirmedCommunityContext
from app.property_records.ingestion import PropertyRecordsIngestion
from app.rpa.core.models import PlatformResult
from app.rpa.core.status import PlatformResultStatus
from app.rpa.utils.logging_utils import setup_logging
from scripts.collect_community_data.models import CommunityCollection


setup_logging()
log = logging.getLogger(__name__)

# 平台注册表：MVP 整改成"URL 直达抓取形式"后在此登记
PLATFORM_MODULES = {
    "ajk": "scripts.collect_community_data.rpa.ajk_mvp_test",
    "ke": "scripts.collect_community_data.rpa.ke_mvp_test",
    "lyj": "scripts.collect_community_data.rpa.lyj_mvp_test",
    "lj": "scripts.collect_community_data.rpa.lj_mvp_test",
    "fang": "scripts.collect_community_data.rpa.fang_mvp_test",
}

EXCEL_SHEET = "房产评估汇总表"
EXCEL_REQUIRED_COLUMNS = ("city", "行政区", "小区名称")


def load_instruction_list(path: Path) -> list[dict]:
    """读取指令清单（Excel 或 JSON），返回去重前的条目列表。

    Excel：房产评估汇总表格式，取 city / 行政区 / 小区名称，
    可选 面积㎡（作为包装上下文与结果透传，不参与默认筛选）；
    JSON：{"city": "深圳", "communities": [{"community_name", "administrative_district", "area"?}]}。
    """
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        city = str(data.get("city") or "").strip()
        communities = data.get("communities")
        if not city or not isinstance(communities, list) or not communities:
            raise ValueError("JSON 清单需要顶层 city 与非空 communities 数组")
        entries = []
        for index, item in enumerate(communities):
            name = str(item.get("community_name") or "").strip()
            district = str(item.get("administrative_district") or "").strip()
            if not name or not district:
                raise ValueError(f"JSON 清单第 {index + 1} 项缺少 community_name 或 administrative_district")
            entries.append(
                {
                    "city": city,
                    "administrative_district": district,
                    "community_name": name,
                    "area": _optional_area(item.get("area")),
                }
            )
        return entries

    if path.suffix.lower() == ".xlsx":
        return load_entries_from_excel(path)

    raise ValueError(f"不支持的清单格式：{path.suffix}（仅支持 .xlsx / .json）")


def _optional_area(value) -> Optional[float]:
    """清单面积列容错解析：空/非数值返回 None。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_entries_from_excel(path: Path) -> list[dict]:
    """读取房产评估汇总表 Excel，取 city / 行政区 / 小区名称，可选 面积㎡。"""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[EXCEL_SHEET] if EXCEL_SHEET in wb.sheetnames else wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        header = [str(cell).strip() if cell is not None else "" for cell in next(rows)]
        try:
            index_city = header.index("city")
            index_district = header.index("行政区")
            index_name = header.index("小区名称")
        except ValueError as exc:
            raise ValueError(f"Excel 表头缺少 {EXCEL_REQUIRED_COLUMNS} 列，实际表头：{header}") from exc
        index_area = header.index("面积㎡") if "面积㎡" in header else None

        entries: list[dict] = []
        for row in rows:
            if row is None:
                continue
            city = str(row[index_city] or "").strip()
            district = str(row[index_district] or "").strip()
            name = str(row[index_name] or "").strip()
            if city and district and name:
                area = _optional_area(row[index_area]) if index_area is not None else None
                entries.append(
                    {
                        "city": city,
                        "administrative_district": district,
                        "community_name": name,
                        "area": area,
                    }
                )
    finally:
        wb.close()

    if not entries:
        raise ValueError("Excel 未读到有效数据行（city/行政区/小区名称 需同时非空）")
    return entries


def resolve_identity(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """与询价编排同口径确认小区身份：仅住宅、唯一命中。

    返回（确认清单, 需人工清单）。确认项携带主数据 record 与清单面积，
    按 community_id 去重（同一小区多套房源只采集一次）。
    """
    confirmed: list[dict] = []
    manual: list[dict] = []
    seen_ids: set[int] = set()
    for entry in entries:
        name = entry["community_name"]
        try:
            records = resolve_communities(entry["city"], entry["administrative_district"], name)
        except Exception as exc:
            manual.append({**entry, "reason": f"主数据查询失败：{exc}"})
            continue
        residential = [r for r in records if r.estate_type == EstateType.RESIDENTIAL.value]
        if not records:
            # resolve 对"多期命中未指定期数"同样返回空列表（与询价口径一致），合并标注
            manual.append({**entry, "reason": "主数据未命中（或多期小区需指定期数）"})
            continue
        if not residential:
            manual.append({**entry, "reason": "小区类型不支持（非住宅）"})
            continue
        if len(residential) != 1:
            manual.append(
                {
                    **entry,
                    "reason": "期数未明确（多条住宅命中）",
                    "candidates": [r.name for r in residential],
                }
            )
            continue
        record = residential[0]
        if record.community_id in seen_ids:
            continue
        seen_ids.add(record.community_id)
        confirmed.append(
            {
                "community_id": record.community_id,
                "community_name": record.name,
                "source_entry": name,
                "city": record.city,
                "administrative_district": record.administrative_district,
                "area": entry.get("area"),
                "record": record,
            }
        )
    return confirmed, manual


def build_contexts(confirmed: list[dict]) -> dict[int, ConfirmedCommunityContext]:
    """包装环节：确认小区构造工程编排同款的 ConfirmedCommunityContext。"""
    contexts: dict[int, ConfirmedCommunityContext] = {}
    for item in confirmed:
        contexts[item["community_id"]] = ConfirmedCommunityContext.from_community_record(
            item["record"],
            area=item.get("area") or 0.0,
            request_id=None,
        )
    return contexts


async def run_platforms(
    confirmed: list[dict], args: argparse.Namespace
) -> tuple[list[CommunityCollection], list[dict]]:
    """逐平台把确认的 community_id 交给 MVP 批量采集。"""
    results: list[CommunityCollection] = []
    platform_errors: list[dict] = []
    community_ids = [item["community_id"] for item in confirmed]
    for platform in args.platforms:
        module = importlib.import_module(PLATFORM_MODULES[platform])
        log.info("===== 平台[%s] 开始抓取（确认小区 %d 个）=====", platform, len(community_ids))
        try:
            summaries = await module.main(
                community_ids=community_ids,
                listing_url=None,
                area=None,
                manual_login=args.manual_login,
                debug=args.debug,
            )
        except Exception as exc:
            log.error("平台[%s] 批量流程异常中断：%s", platform, exc)
            platform_errors.append({"platform": platform, "error": str(exc)})
            continue
        name_by_id = {item["community_id"]: item["community_name"] for item in confirmed}
        for item in summaries:
            # platform 已由 MVP 填写；community_name 保持平台页面名（记录层
            # source_community_name），主数据正式名回填 canonical_name
            item.canonical_name = name_by_id.get(item.community_id, "")
        results.extend(summaries)
        blocked = sum(1 for item in summaries if item.blocked_reason)
        log.info(
            "平台[%s] 完成：小区 %d 个，被拦 %d 个，在售快照 %d 条",
            platform,
            len(summaries),
            blocked,
            sum(len(item.listings) for item in summaries),
        )
    return results, platform_errors


def record_stage(
    results: list[CommunityCollection],
    contexts: dict[int, ConfirmedCommunityContext],
    dry_run: bool,
) -> dict:
    """记录环节：CommunityCollection 与工程 PlatformResult 同构，直接映射入库。

    ajk 现阶段无成交明细、挂牌快照缺详情链接（listing_records 置空），
    实际入库物为入口行补全；详情链接方案确定后填充 listing_records 即可。
    被拦小区与直传 URL（无身份上下文）跳过记录；异常不中断批次。
    """
    report: dict = {"dry_run": dry_run, "recorded": 0, "errors": [], "skipped": []}
    if dry_run:
        for item in results:
            if item.blocked_reason or not item.listing_page_url:
                continue
            report["recorded"] += 1
            log.info(
                "[dry-run] 将记录 %s(%s) 平台=%s 在售 %d 条 成交 %d 条",
                item.community_name,
                item.community_id,
                item.platform,
                len(item.listings),
                len(item.deals),
            )
        return report

    ingestion = PropertyRecordsIngestion()
    for item in results:
        if item.blocked_reason or not item.listing_page_url:
            report["skipped"].append(
                {
                    "community_name": item.community_name,
                    "platform": item.platform,
                    "reason": item.blocked_reason or "缺少挂牌入口",
                }
            )
            continue
        context = contexts.get(item.community_id)
        if context is None:
            report["skipped"].append(
                {"community_name": item.community_name, "platform": item.platform, "reason": "直传 URL 无身份上下文"}
            )
            continue

        platform_result = PlatformResult(
            name=item.platform,
            status=PlatformResultStatus(item.status),
            community_avg_price=item.community_avg_price,
            quote_prices=[s.unit_price for s in item.listings if s.unit_price],
            listing_snapshots=item.listings,
            deal_records=item.deals,
            deal_source="成交记录" if item.deals else "无",
        )
        try:
            ingestion.ingest_rpa_result(
                community_id=item.community_id,
                city=context.city,
                administrative_district=context.administrative_district,
                source_community_name=item.community_name,
                result=platform_result,
                listing_page_url=item.listing_page_url,
                deal_page_url=item.deal_page_url,
                listing_records=[],
            )
            report["recorded"] += 1
        except Exception as exc:
            log.error(
                "记录失败：%s(%s) 平台=%s：%s", item.community_name, item.community_id, item.platform, exc
            )
            report["errors"].append(
                {
                    "community_name": item.community_name,
                    "platform": item.platform,
                    "community_id": item.community_id,
                    "error": str(exc),
                }
            )
    return report


def evaluate_stage(results: list[CommunityCollection]) -> list[dict]:
    """筛选计算环节：工程 evaluate_algorithm（加权落点中位数）。

    对未被拦的小区逐个计算在售均价与最终价；被拦小区不参与计算。
    """
    evaluations: list[dict] = []
    for item in results:
        if item.blocked_reason:
            continue
        prices = [s.unit_price for s in item.listings if s.unit_price]
        entry: dict = {
            "platform": item.platform,
            "community_id": item.community_id,
            "community_name": item.canonical_name or item.community_name,
            "listing_count": len(item.listings),
            "listing_avg": sum(prices) / len(prices) if prices else None,
            "community_avg_price": item.community_avg_price,
            "deal_count": len(item.deals),
        }
        if prices:
            evaluation = evaluate_algorithm(
                inputs=AlgorithmInput(
                    quote_price_lists=[prices],
                    weighted_median_discount=get_weighted_median_discount(),
                ),
            )
            entry.update(
                {
                    "deal_avg": evaluation.deal_avg,
                    "final_price": evaluation.decision.final_price,
                    "branch": evaluation.decision.branch,
                }
            )
        evaluations.append(entry)
    return evaluations


def write_result_file(path: Path, payload: dict) -> None:
    """把聚合结果写为 JSON 台账。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写 {path}")


def print_report(
    entries: list[dict],
    manual: list[dict],
    results: list[CommunityCollection],
    evaluations: list[dict],
    platform_errors: list[dict],
    record_report: dict,
) -> None:
    """打印身份确认、抓取与计算、记录汇总。"""
    print("\n===== 身份确认 =====")
    print(f"清单条目 {len(entries)} 条，确认 {len(entries) - len(manual)} 条（按小区去重后采集），需人工 {len(manual)} 条")
    for item in manual:
        candidates = f"，候选：{'、'.join(item['candidates'])}" if item.get("candidates") else ""
        print(f"[需人工] {item['city']}/{item['administrative_district']}/{item['community_name']}：{item['reason']}{candidates}")

    eval_by_key = {(e["platform"], e["community_id"]): e for e in evaluations}
    print("\n===== 抓取与计算汇总 =====")
    for item in results:
        location = f"[{item.platform}] {item.canonical_name or item.community_name}"
        if item.blocked_reason:
            print(f"[被拦] {location}：{item.blocked_reason}")
            continue
        evaluation = eval_by_key.get((item.platform, item.community_id), {})
        print(
            f"[完成] {location}：在售 {len(item.listings)} 条，"
            f"在售均价 {evaluation.get('listing_avg') or '未识别'}，"
            f"挂牌均价 {item.community_avg_price or '未识别'}，"
            f"算法最终价 {evaluation.get('final_price') or '-'}"
        )
    for item in platform_errors:
        print(f"[平台中断] [{item['platform']}]：{item['error']}")

    print("\n===== 记录汇总 =====")
    mode = "dry-run 预览" if record_report["dry_run"] else "实际写入"
    print(f"模式：{mode}；记录 {record_report['recorded']} 条")
    for item in record_report["skipped"]:
        print(f"[跳过记录] [{item['platform']}] {item['community_name']}：{item['reason']}")
    for item in record_report["errors"]:
        print(f"[记录失败] [{item['platform']}] {item['community_name']}：{item['error']}")


def main() -> int:
    """解析指令并串起“清单 → 鉴定 → 包装 → 逐平台抓取 → 记录 → 汇总”。"""
    parser = argparse.ArgumentParser(
        description="小区数据抓取编排层（指令源 = Excel/JSON；鉴定/包装/记录/算法均调工程件；当前仅 ajk）"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="清单文件：房产评估汇总表 .xlsx 或 .json（格式见文件头 docstring）",
    )
    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=sorted(PLATFORM_MODULES),
        default=sorted(PLATFORM_MODULES),
        help="平台代码，缺省全部已接入平台",
    )
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="指令必带：首页打开后人工过验证码 / 登录，回车确认后开始采集。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="采集与算法照跑，跳过记录层（不写 property_records）。",
    )
    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML。",
    )
    parser.add_argument(
        "--result-file",
        default=None,
        help="结果 JSON 路径；缺省 results/community_collect/collect_<时间戳>.json",
    )
    args = parser.parse_args()

    input_path = Path(args.input) if Path(args.input).is_absolute() else PROJECT_ROOT / args.input
    try:
        entries = load_instruction_list(input_path)
    except ValueError as exc:
        print(f"[清单错误] {exc}")
        return 1

    # 鉴定
    confirmed, manual = resolve_identity(entries)
    log.info(
        "身份确认：清单 %d 条 → 确认小区 %d 个，需人工 %d 条",
        len(entries),
        len(confirmed),
        len(manual),
    )
    if not confirmed:
        print("[错误] 清单中没有可确认身份的小区，未发起抓取")
        print_report(entries, manual, [], [], {"dry_run": True, "recorded": 0, "errors": [], "skipped": []})
        return 1

    # 包装
    contexts = build_contexts(confirmed)
    log.info("包装：已构造 %d 个 ConfirmedCommunityContext", len(contexts))

    # RPA（MVP 直达采集）
    try:
        results, platform_errors = uc.loop().run_until_complete(run_platforms(confirmed, args))
    except KeyboardInterrupt:
        # 终端 Ctrl+C：事件循环已冻结，MVP 协程的 finally 不会执行，
        # 在进程退出层逐平台同步终止常驻浏览器
        for platform in args.platforms:
            try:
                terminate_browser = getattr(
                    importlib.import_module(PLATFORM_MODULES[platform]),
                    "terminate_browser",
                )
            except (ImportError, AttributeError):
                continue
            try:
                terminate_browser()
            except Exception as exc:
                log.debug("平台[%s] 浏览器终止失败：%s", platform, exc)
        raise

    # 记录
    record_report = record_stage(results, contexts, args.dry_run)

    # 筛选计算
    evaluations = evaluate_stage(results)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input_file": str(input_path),
        "platforms": args.platforms,
        "dry_run": args.dry_run,
        "identity": {
            "confirmed": [
                {k: v for k, v in item.items() if k != "record"}
                for item in confirmed
            ],
            "manual": manual,
        },
        "results": [asdict(item) for item in results],
        "evaluations": evaluations,
        "platform_errors": platform_errors,
        "record_report": record_report,
        "note": "挂牌入库待详情链接方案确定后接入 listing_records；算法结果在 results[].final_price",
    }
    result_path = Path(args.result_file) if args.result_file else (
        PROJECT_ROOT / "results" / "community_collect" / f"collect_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    write_result_file(result_path, payload)
    print_report(entries, manual, results, evaluations, platform_errors, record_report)
    log.info(
        "抓取完成：小区结果 %d 条，记录 %d 条，计算 %d 条，身份需人工 %d 条，平台中断 %d 个",
        len(results),
        record_report["recorded"],
        len(evaluations),
        len(manual),
        len(platform_errors),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
