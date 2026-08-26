# -*- coding: utf-8 -*-
"""重处理缺失建成年份的房天下详情页，并按批次聚合写回 SQLite。"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_detail_retry_queue_20260825.json"
DEFAULT_RAW = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_detail_results.jsonl"
DEFAULT_RETRY_RAW = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_detail_retry_results_20260825.jsonl"
DEFAULT_PROGRESS = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_detail_retry_progress.json"
DEFAULT_DATABASE = PROJECT_ROOT / "persist" / "community_data.sqlite3"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
YEAR_RE = re.compile(r"建筑年代\s*[:：]?\s*((?:19|20)\d{2})")
CRUMB_RE = re.compile(r"([^\n>]+)小区二手房\s*>\s*([^\n>]+)小区二手房\s*>\s*([^\n>]+)\s*小区首页")
CAPTCHA_TEXT_MARKERS = (
    "安全验证",
    "请完成验证",
    "人机验证",
    "验证后继续",
    "完成验证",
    "滑动验证",
    "访问过于频繁",
    "验证码校验",
    "请输入验证",
)
CAPTCHA_URL_MARKERS = ("/captcha", "captcha", "verifycode", "antibot", "antispam")
BUSINESS_TEXT_MARKERS = (
    "建筑年代",
    "基本信息",
    "小区地址",
    "所在区域",
    "元/㎡",
    "元/平米",
    "热门房源",
)


def normalize(value: Any) -> str:
    return re.sub(r"[\s()（）\[\]【】·•,，。]", "", str(value or "")).strip().casefold()


def area_key(value: Any) -> str:
    return str(value or "").strip().removesuffix("区")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(item, ensure_ascii=False) + "\n")
        output.flush()


def latest_by_url(path: Path) -> dict[str, dict[str, Any]]:
    return {str(item["detail_url"]): item for item in load_jsonl(path) if item.get("detail_url")}


def fetch_detail(session: requests.Session, url: str, timeout: float) -> tuple[str, str, str]:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    html = response.text
    text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
    return response.url, html, text


def parse_detail(item: dict[str, Any], final_url: str, text: str) -> dict[str, Any]:
    breadcrumb = CRUMB_RE.search(text)
    area = breadcrumb.groups() if breadcrumb else (None, None, None)
    detail_name = str(area[2] or "").strip() or None
    build_match = YEAR_RE.search(text)
    build_year = int(build_match.group(1)) if build_match else None
    result: dict[str, Any] = {
        "source_name": item.get("source_name"),
        "detail_url": item.get("detail_url"),
        "detail_name": detail_name,
        "detail_administrative_district": str(area[0] or "").strip() or None,
        "detail_district": str(area[1] or "").strip() or None,
        "build_year": build_year,
        "checked_at": now(),
        "status": "MISMATCH",
        "community_id": None,
        "reason": None,
    }
    final_url_lower = (final_url or "").lower()
    if any(marker in final_url_lower for marker in CAPTCHA_URL_MARKERS) or "check." in final_url_lower:
        result["status"] = "CAPTCHA_OR_VERIFICATION"
        result["reason"] = "房天下返回验证页，停止重处理"
        return result
    # 普通详情页会有“手机号 获取验证码/请输入验证码”咨询表单；只有页面同时
    # 缺失所有详情业务字段时，才把验证话术认定为拦截页。
    has_captcha_text = any(marker in text for marker in CAPTCHA_TEXT_MARKERS)
    has_business_text = any(marker in text for marker in BUSINESS_TEXT_MARKERS)
    if has_captcha_text and not has_business_text:
        result["status"] = "CAPTCHA_OR_VERIFICATION"
        result["reason"] = "房天下页面出现验证码或人机验证，停止重处理"
        return result

    candidates = [
        candidate
        for candidate in item.get("candidate_records", [])
        if area_key(candidate.get("administrative_district")) == area_key(area[0])
        and any(
            normalize(detail_name) == normalize(candidate_name)
            for candidate_name in (
                candidate.get("name"),
                *(candidate.get("aliases") or []),
            )
        )
    ]
    if len(candidates) > 1 and area[1]:
        district_matches = [
            candidate
            for candidate in candidates
            if normalize(candidate.get("district")) == normalize(area[1])
        ]
        if len(district_matches) == 1:
            candidates = district_matches
    if len(candidates) != 1:
        result["status"] = "AMBIGUOUS" if len(candidates) > 1 else "MISMATCH"
        result["reason"] = "行政区内有多个同名主记录" if len(candidates) > 1 else "行政区或主名称不匹配"
        return result
    result["community_id"] = candidates[0]["community_id"]
    if build_year is None or not 1800 <= build_year <= 2026:
        result["status"] = "NO_YEAR"
        result["reason"] = "房天下详情未标注四位建成年份"
    else:
        result["status"] = "SUCCESS"
    return result


def run_aggregate() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/community_data/aggregate_fang_build_year.py"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        return {"aggregate_error": (exc.stderr or exc.stdout or str(exc)).strip()[-500:]}
    for line in reversed(completed.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def save_progress(
    path: Path,
    queue_size: int,
    latest: dict[str, dict[str, Any]],
    queue_urls: set[str],
    target_ids: int,
    unresolved_ids: int,
    stopped_reason: str | None,
) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    scoped = {url: item for url, item in latest.items() if url in queue_urls}
    for item in scoped.values():
        counts[str(item.get("status"))] += 1
    final_statuses = {"SUCCESS", "NO_YEAR", "MISMATCH", "AMBIGUOUS", "CAPTCHA_OR_VERIFICATION"}
    completed = sum(1 for item in scoped.values() if item.get("status") in final_statuses)
    resolved_ids = {
        int(item["community_id"])
        for item in scoped.values()
        if item.get("status") == "SUCCESS" and str(item.get("community_id") or "").isdigit()
    }
    payload = {
        "total": queue_size,
        "completed": completed,
        "pending": queue_size - completed,
        "attempted_urls": len(scoped),
        "status_counts": dict(counts),
        "target_ids": target_ids,
        "unresolved_ids": max(0, target_ids - len(resolved_ids)),
        "stopped_reason": stopped_reason,
        "updated_at": now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重处理房天下缺失建成年份记录")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--interval", type=float, default=0.8)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument(
        "--retry-captcha",
        action="store_true",
        help="重试此前因验证码或人机验证停止的详情页；再次出现时仍立即停止",
    )
    parser.add_argument(
        "--retry-mismatches",
        action="store_true",
        help="重新核验此前名称/行政区不匹配的记录（用于修正别名匹配）",
    )
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--retry-raw", type=Path, default=DEFAULT_RETRY_RAW)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    queue_urls = {str(item["detail_url"]) for item in queue}
    prior = latest_by_url(args.retry_raw)
    terminal = {"SUCCESS", "NO_YEAR", "MISMATCH", "AMBIGUOUS"}
    if not args.retry_captcha:
        terminal.add("CAPTCHA_OR_VERIFICATION")
    if args.retry_mismatches:
        terminal.discard("MISMATCH")
        terminal.discard("AMBIGUOUS")
    target_ids = {
        int(candidate["community_id"])
        for item in queue
        for candidate in item.get("candidate_records", [])
        if candidate.get("community_id") is not None
    }
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
    stopped_reason: str | None = None
    retry_once: set[str] = set()
    if args.retry_captcha:
        retry_once.update(
            url for url, result in prior.items() if result.get("status") == "CAPTCHA_OR_VERIFICATION"
        )
    if args.retry_mismatches:
        retry_once.update(
            url
            for url, result in prior.items()
            if result.get("status") in {"MISMATCH", "AMBIGUOUS"}
        )
    attempted_this_run: set[str] = set()
    batches = 0
    while True:
        pending = [
            item
            for item in queue
            if item["detail_url"] not in attempted_this_run
            and (
                prior.get(item["detail_url"], {}).get("status") not in terminal
                or item["detail_url"] in retry_once
            )
        ]
        if not pending or (args.max_batches is not None and batches >= args.max_batches):
            break
        batch = pending[: max(1, args.batch_size)]
        processed_in_batch = 0
        for item in batch:
            detail_url = item["detail_url"]
            attempted_this_run.add(detail_url)
            retry_once.discard(detail_url)
            try:
                final_url, _html, text = fetch_detail(session, detail_url, args.timeout)
                result = parse_detail(item, final_url, text)
            except Exception as exc:  # 网络失败保留可重试状态，不计为完成
                result = {
                    "source_name": item.get("source_name"),
                    "detail_url": item.get("detail_url"),
                    "detail_name": None,
                    "detail_administrative_district": None,
                    "detail_district": None,
                    "build_year": None,
                    "checked_at": now(),
                    "status": "FETCH_ERROR",
                    "community_id": None,
                    "reason": str(exc),
                }
            append_jsonl(args.retry_raw, result)
            append_jsonl(args.raw, result)
            prior[item["detail_url"]] = result
            processed_in_batch += 1
            if result["status"] == "CAPTCHA_OR_VERIFICATION":
                stopped_reason = result["reason"]
                break
            time.sleep(max(0.0, args.interval))
        batches += 1
        aggregate = run_aggregate()
        progress = save_progress(args.progress, len(queue), prior, queue_urls, len(target_ids), 0, stopped_reason)
        print(
            json.dumps(
                {
                    "batch": batches,
                    "processed": processed_in_batch,
                    "batch_success": sum(1 for item in batch if prior[item["detail_url"]].get("status") == "SUCCESS"),
                    "sqlite_updated": aggregate.get("sqlite_updated", 0),
                    "aggregated": aggregate.get("aggregated", 0),
                    **progress,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if stopped_reason:
            break
    aggregate = run_aggregate()
    progress = save_progress(args.progress, len(queue), prior, queue_urls, len(target_ids), 0, stopped_reason)
    print(
        json.dumps(
            {
                "finished": True,
                "sqlite_updated": aggregate.get("sqlite_updated", 0),
                "aggregated": aggregate.get("aggregated", 0),
                **progress,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
