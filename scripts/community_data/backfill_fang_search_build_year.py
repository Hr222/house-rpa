# -*- coding: utf-8 -*-
"""仅为缺失建成年份的小区搜索房天下并严格回填。"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "persist" / "community_data.sqlite3"
DEFAULT_RAW = (
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_search_detail_results_20260826.jsonl"
)
DEFAULT_PROGRESS = (
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_search_detail_progress_20260826.json"
)
DEFAULT_AGGREGATE_RESULTS = (
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "fang_search_build_year_backfill_20260826.jsonl"
)
HOME_URL = "https://sz.esf.fang.com/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
YEAR_RE = re.compile(r"建筑年代\s*[:：]?\s*((?:19|20)\d{2})\s*年")
CAPTCHA_TEXT_MARKERS = (
    "请完成下列验证后继续",
    "拖动滑块验证",
    "安全验证",
    "访问过于频繁",
    "验证后继续访问",
)
DETAIL_TEXT_MARKERS = ("建筑年代", "楼栋总数", "房屋总数", "小区首页", "小区详情")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value: Any) -> str:
    return re.sub(r"[\s()（）\[\]【】·•,，。]", "", str(value or "")).casefold()


def area_key(value: Any) -> str:
    return str(value or "").strip().removesuffix("区")


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(item, ensure_ascii=False))
        output.write("\n")
        output.flush()


def latest_by_community(path: Path) -> dict[int, dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            community_id = int(item["community_id"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        latest[community_id] = item
    return latest


def pending_records(database: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "select community_id, administrative_district, district, name, aliases_json "
            "from communities where build_year is null order by community_id"
        ).fetchall()
    finally:
        connection.close()
    records: list[dict[str, Any]] = []
    for community_id, administrative_district, district, name, aliases_json in rows:
        try:
            aliases = json.loads(aliases_json or "[]")
        except json.JSONDecodeError:
            aliases = []
        records.append(
            {
                "community_id": int(community_id),
                "administrative_district": str(administrative_district or ""),
                "district": str(district or ""),
                "name": str(name or ""),
                "aliases": [str(value) for value in aliases if str(value or "").strip()],
            }
        )
    return records


def database_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(database)
    try:
        total, filled, pending = connection.execute(
            "select count(*), sum(case when build_year is not null then 1 else 0 end), "
            "sum(case when build_year is null then 1 else 0 end) from communities"
        ).fetchone()
    finally:
        connection.close()
    return {"database_total": int(total), "database_filled": int(filled or 0), "database_pending": int(pending or 0)}


def is_verification(final_url: str, text: str) -> bool:
    url = (final_url or "").lower()
    if "check.3g.fang.com/check" in url or any(marker in url for marker in ("captcha", "verifycode", "antibot", "antispam")):
        return True
    return any(marker in text for marker in CAPTCHA_TEXT_MARKERS) and not any(
        marker in text for marker in DETAIL_TEXT_MARKERS
    )


def request_search_page(session: requests.Session, query_name: str, timeout: float) -> tuple[str, str]:
    home = session.get(HOME_URL, timeout=timeout)
    home.raise_for_status()
    soup = BeautifulSoup(home.text, "html.parser")
    form = soup.select_one("form#form_esf")
    if form is None or not form.get("action"):
        raise RuntimeError("房天下首页未找到小区搜索表单")
    data = {
        str(element.get("name")): str(element.get("value") or "")
        for element in form.select("input[name]")
        if element.get("name")
    }
    data["input_keyw1"] = query_name
    response = session.post(urljoin(HOME_URL, str(form["action"])), data=data, timeout=timeout)
    response.raise_for_status()
    return response.url, response.text


def parse_search_candidates(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    output: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for card in soup.select("ul.mar100"):
        link = card.select_one("a[href*='/loupan/']")
        if link is None:
            continue
        detail_url = urljoin(HOME_URL, str(link.get("href") or ""))
        name = link.get_text(" ", strip=True)
        location = card.select_one("li.col14")
        location_parts = [part.strip() for part in location.get_text(" ", strip=True).split("|")] if location else []
        administrative_district = location_parts[0] if location_parts else ""
        district = location_parts[1] if len(location_parts) > 1 else ""
        if not detail_url or not name or detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)
        output.append(
            {
                "detail_url": detail_url,
                "name": name,
                "administrative_district": administrative_district,
                "district": district,
            }
        )
    return output


def record_names(record: dict[str, Any]) -> list[str]:
    values = [record["name"], *record["aliases"]]
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize(value)
        if key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def matching_search_candidates(record: dict[str, Any], candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    names = {normalize(value) for value in record_names(record)}
    return [
        candidate
        for candidate in candidates
        if area_key(candidate["administrative_district"]) == area_key(record["administrative_district"])
        and normalize(candidate["name"]) in names
    ]


def parse_detail(final_url: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    title = soup.select_one(".title_village h3")
    aliases = soup.select_one(".title_village .name_village")
    current = soup.select_one(".current")
    breadcrumb = current.get_text(" ", strip=True) if current else ""
    areas = re.findall(r"([^\s>]+)二手房", breadcrumb)
    administrative_district = areas[-2] if len(areas) >= 2 else ""
    district = areas[-1] if len(areas) >= 1 else ""
    alias_values = []
    if aliases:
        alias_text = aliases.get_text(" ", strip=True).removeprefix("别名：")
        alias_values = [value.strip() for value in re.split(r"[、,，]", alias_text) if value.strip()]
    year_match = YEAR_RE.search(text)
    return {
        "final_url": final_url,
        "text": text,
        "detail_name": title.get_text(" ", strip=True) if title else "",
        "detail_aliases": alias_values,
        "detail_administrative_district": administrative_district,
        "detail_district": district,
        "build_year": int(year_match.group(1)) if year_match else None,
    }


def fetch_detail(session: requests.Session, url: str, timeout: float) -> tuple[str, str]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.url, response.text


def result_for_record(session: requests.Session, record: dict[str, Any], timeout: float) -> dict[str, Any]:
    base = {
        "community_id": record["community_id"],
        "source_name": "房天下搜索详情页",
        "record_name": record["name"],
        "detail_url": None,
        "detail_name": None,
        "detail_administrative_district": None,
        "detail_district": None,
        "build_year": None,
        "checked_at": now(),
        "status": "MISMATCH",
        "reason": None,
    }
    all_matches: dict[str, dict[str, str]] = {}
    queries = [record["name"]]
    if not record["name"].strip():
        queries = []
    for alias in record["aliases"]:
        if normalize(alias) and len(normalize(alias)) >= 4:
            queries.append(alias)
    for query_name in record_names({**record, "aliases": queries[1:] if queries else []}):
        search_url, search_html = request_search_page(session, query_name, timeout)
        search_text = " ".join(BeautifulSoup(search_html, "html.parser").get_text(" ", strip=True).split())
        if is_verification(search_url, search_text):
            base.update(status="CAPTCHA_OR_VERIFICATION", reason="房天下搜索页出现真实验证")
            return base
        matches = matching_search_candidates(record, parse_search_candidates(search_html))
        for candidate in matches:
            all_matches[candidate["detail_url"]] = candidate
        if all_matches:
            break
    if len(all_matches) != 1:
        base.update(
            status="AMBIGUOUS" if len(all_matches) > 1 else "MISMATCH",
            reason="房天下搜索存在多个严格候选" if len(all_matches) > 1 else "房天下搜索未找到行政区和名称均一致的小区",
        )
        return base
    candidate = next(iter(all_matches.values()))
    final_url, detail_html = fetch_detail(session, candidate["detail_url"], timeout)
    detail = parse_detail(final_url, detail_html)
    base.update(
        detail_url=final_url,
        detail_name=detail["detail_name"] or None,
        detail_administrative_district=detail["detail_administrative_district"] or None,
        detail_district=detail["detail_district"] or None,
    )
    if is_verification(final_url, detail["text"]):
        base.update(status="CAPTCHA_OR_VERIFICATION", reason="房天下详情页出现真实验证")
        return base
    detail_names = {normalize(detail["detail_name"]), *(normalize(alias) for alias in detail["detail_aliases"])}
    requested_names = {normalize(value) for value in record_names(record)}
    if (
        area_key(detail["detail_administrative_district"]) != area_key(record["administrative_district"])
        or not detail_names.intersection(requested_names)
    ):
        base.update(status="MISMATCH", reason="房天下详情行政区或主名称不匹配")
        return base
    year = detail["build_year"]
    if year is None or not 1800 <= year <= 2026:
        base.update(status="NO_YEAR", reason="房天下详情未标注四位建成年份")
        return base
    base.update(status="SUCCESS", build_year=year)
    return base


def run_aggregate(raw: Path, results: Path, database: Path) -> dict[str, Any]:
    from aggregate_fang_build_year import (  # Imported locally to keep this script executable from project root.
        aggregate_fang_results,
        apply_successful_results,
        load_jsonl,
        manual_confirmation_ids,
        write_results,
    )
    manual = PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "build_year_manual_confirmations.jsonl"
    values = aggregate_fang_results(load_jsonl(raw), manual_confirmation_ids(manual))
    write_results(results, values)
    from app.community_data.database import CommunityDatabase

    updated = apply_successful_results(CommunityDatabase(database), values)
    return {
        "aggregated": len(values),
        "aggregate_success": sum(item["status"] == "SUCCESS" for item in values),
        "manual_review": sum(item["status"] == "MANUAL_REVIEW" for item in values),
        "sqlite_updated": updated,
    }


def save_progress(
    path: Path,
    total: int,
    latest: dict[int, dict[str, Any]],
    database: Path,
    stopped_reason: str | None,
) -> dict[str, Any]:
    terminal = {"SUCCESS", "NO_YEAR", "MISMATCH", "AMBIGUOUS", "CAPTCHA_OR_VERIFICATION"}
    counts = Counter(str(item.get("status")) for item in latest.values())
    completed = sum(item.get("status") in terminal for item in latest.values())
    payload = {
        "total_targeted": total,
        "completed": completed,
        "pending": max(0, total - completed),
        "status_counts": dict(counts),
        "stopped_reason": stopped_reason,
        "updated_at": now(),
        **database_counts(database),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="仅回填缺失建成年份的房天下搜索详情页数据")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--aggregate-results", type=Path, default=DEFAULT_AGGREGATE_RESULTS)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--interval", type=float, default=0.8)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--retry-captcha",
        action="store_true",
        help="在用户已完成房天下真实验证后重试此前停下的记录",
    )
    parser.add_argument(
        "--manual-result",
        type=Path,
        help="追加一条已在浏览器中人工完成验证并核对的详情结果 JSON",
    )
    args = parser.parse_args()
    if args.batch_size <= 0 or args.interval < 0 or args.timeout <= 0:
        raise SystemExit("batch-size、interval 和 timeout 参数无效")

    if args.manual_result:
        append_jsonl(args.raw, json.loads(args.manual_result.read_text(encoding="utf-8")))

    records = pending_records(args.database)
    if args.limit is not None:
        records = records[: max(0, args.limit)]
    latest = latest_by_community(args.raw)
    terminal = {"SUCCESS", "NO_YEAR", "MISMATCH", "AMBIGUOUS", "CAPTCHA_OR_VERIFICATION"}
    if args.retry_captcha:
        terminal.discard("CAPTCHA_OR_VERIFICATION")
    todo = [record for record in records if latest.get(record["community_id"], {}).get("status") not in terminal]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
    stopped_reason: str | None = None
    for batch_number, offset in enumerate(range(0, len(todo), args.batch_size), start=1):
        batch = todo[offset : offset + args.batch_size]
        batch_counts: Counter[str] = Counter()
        for record in batch:
            try:
                result = result_for_record(session, record, args.timeout)
            except Exception as exc:  # Network failures stay non-terminal for the next run.
                result = {
                    "community_id": record["community_id"],
                    "source_name": "房天下搜索详情页",
                    "record_name": record["name"],
                    "detail_url": None,
                    "detail_name": None,
                    "detail_administrative_district": None,
                    "detail_district": None,
                    "build_year": None,
                    "checked_at": now(),
                    "status": "FETCH_ERROR",
                    "reason": str(exc),
                }
            append_jsonl(args.raw, result)
            latest[record["community_id"]] = result
            batch_counts[str(result["status"])] += 1
            if result["status"] == "CAPTCHA_OR_VERIFICATION":
                stopped_reason = str(result["reason"])
                break
            time.sleep(args.interval)
        aggregate = run_aggregate(args.raw, args.aggregate_results, args.database)
        progress = save_progress(args.progress, len(records), latest, args.database, stopped_reason)
        print(json.dumps({"batch": batch_number, "processed": sum(batch_counts.values()), "batch_status_counts": dict(batch_counts), **aggregate, **progress}, ensure_ascii=False), flush=True)
        if stopped_reason:
            break
    if not todo:
        progress = save_progress(args.progress, len(records), latest, args.database, None)
        print(json.dumps({"finished": True, **progress}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
