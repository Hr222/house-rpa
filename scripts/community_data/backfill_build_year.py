# -*- coding: utf-8 -*-
"""按 Q 房网公开小区页批量回填深圳小区建成年份。

只在来源年份跨度不超过一年时写入 SQLite；跨度超过一年保留为空，
由 Excel 同步脚本标记为人工核对。每条结果追加到 JSONL，支持中断后继续。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterable

from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import CommunityDatabase
from app.community_data.models import CommunityRecord
from app.community_data.normalization import normalize_name


log = logging.getLogger(__name__)

SOURCE_NAME = "Q房网"
SOURCE_BASE_URL = "https://shenzhen.qfang.com"
DEFAULT_RESULTS_PATH = (
    PROJECT_ROOT / "outputs" / "xqdata_excel_20260824" / "build_year_backfill.jsonl"
)
REFERENCE_YEAR = 2026
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.0
DEFAULT_RETRIES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
)

QFANG_DISTRICT_PATHS = {
    "南山区": "nanshan",
    "福田区": "futian",
    "罗湖区": "luohu",
    "宝安区": "baoan",
    "龙岗区": "longgang",
    "龙华区": "longhuaa",
    "光明区": "guangmingqu",
    "盐田区": "yantiana",
    "坪山区": "pingshanab",
    "大鹏新区": "dapengxinqu",
    "深汕特别合作区": "shenshanhezuoquab",
}
YEAR_TEXT_RE = re.compile(
    r"(?P<first>(?:19|20)\d{2})\s*年"
    r"(?:\s*[-~至]\s*(?P<last>(?:19|20)\d{2})\s*年)?\s*(?:建|竣工|交付)"
)
BUILD_YEAR_CONTEXT_RE = re.compile(
    r"(?:建筑年代|建成年代|竣工时间)\s*[:：]?\s*"
    r"(?P<first>(?:19|20)\d{2})\s*年"
    r"(?:\s*[-~至]\s*(?P<last>(?:19|20)\d{2})\s*年)?"
)


@dataclass(frozen=True)
class QfangCandidate:
    """Q 房网小区搜索结果中的一条小区卡片。"""

    name: str
    aliases: tuple[str, ...]
    detail_url: str
    evidence: str
    years: tuple[int, ...]


@dataclass(frozen=True)
class BackfillResult:
    """供 Excel 同步脚本消费的一条年份核验结果。"""

    community_id: int
    status: str
    build_year: int | None
    candidate_years: tuple[int, ...]
    rule: str
    evidence: str
    source_urls: tuple[str, ...]
    checked_at: str
    message: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class RequestRateLimiter:
    """对 Q 房网请求进行单线程节流。"""

    def __init__(self, interval_seconds: float) -> None:
        self._interval_seconds = interval_seconds
        self._next_request_at = 0.0
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            scheduled_at = max(now, self._next_request_at)
            self._next_request_at = scheduled_at + self._interval_seconds
        wait_seconds = scheduled_at - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)


def parse_qfang_years(text: str) -> tuple[int, ...]:
    """从 Q 房网卡片或详情文本中提取明确标注的建成年份。"""
    matches = list(YEAR_TEXT_RE.finditer(text))
    matches.extend(BUILD_YEAR_CONTEXT_RE.finditer(text))
    years: set[int] = set()
    for match in matches:
        for value in (match.group("first"), match.group("last")):
            if value is None:
                continue
            year = int(value)
            if 1800 <= year <= REFERENCE_YEAR:
                years.add(year)
    return tuple(sorted(years))


def parse_qfang_candidates(html: str) -> list[QfangCandidate]:
    """解析 Q 房网小区搜索页，排除页面中的推荐新房等无关卡片。"""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[QfangCandidate] = []
    seen_urls: set[str] = set()
    for card in soup.select(".list-result li.items"):
        title_link = card.select_one(".list-main-header a.house-title[href]")
        if title_link is None:
            continue
        href = str(title_link.get("href") or "").strip()
        if "/garden/desc/" not in href:
            continue
        detail_url = urllib.parse.urljoin(SOURCE_BASE_URL, href)
        if detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)

        name = title_link.get_text(" ", strip=True)
        if not name:
            continue
        alias_element = card.select_one(".list-main-header .alias-name")
        aliases = tuple(
            value.strip("()（） ")
            for value in (alias_element.get_text(" ", strip=True).split("、") if alias_element else [])
            if value.strip("()（） ")
        )
        evidence = " ".join(card.get_text(" ", strip=True).split())
        years = parse_qfang_years(evidence)
        candidates.append(
            QfangCandidate(
                name=name,
                aliases=aliases,
                detail_url=detail_url,
                evidence=evidence,
                years=years,
            )
        )
    return candidates


def choose_build_year(years: Iterable[int]) -> tuple[str, int | None, str]:
    """执行 2026 年确定的年份规则，返回状态、最终年份和规则标识。"""
    unique_years = tuple(sorted({int(year) for year in years}))
    if not unique_years:
        return "PENDING", None, "QFANG_BUILD_YEAR_NOT_FOUND"
    if unique_years[-1] - unique_years[0] > 1:
        return "MANUAL_REVIEW", None, "QFANG_REPORTED_YEAR_RANGE_OVER_1"
    return "SUCCESS", unique_years[-1], "QFANG_LATEST_WITHIN_1_YEAR"


def _district_key(administrative_district: str) -> str:
    return str(administrative_district or "").strip()


def _district_matches(candidate: QfangCandidate, administrative_district: str) -> bool:
    district = _district_key(administrative_district).removesuffix("区")
    if not district:
        return True
    return district in candidate.evidence


def _same_community_name(left: str, right: str) -> bool:
    """严格优先；别名允许四字以上的包含匹配以处理“某某大厦”后缀。"""
    normalized_left = normalize_name(left)
    normalized_right = normalize_name(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    shortest = min(len(normalized_left), len(normalized_right))
    return shortest >= 4 and (
        normalized_left in normalized_right or normalized_right in normalized_left
    )


def _candidate_match_score(candidate: QfangCandidate, record: CommunityRecord) -> int:
    record_names = (record.name, *record.aliases)
    candidate_names = (candidate.name, *candidate.aliases)
    exact = any(
        normalize_name(record_name) == normalize_name(candidate_name)
        for record_name in record_names
        for candidate_name in candidate_names
        if normalize_name(record_name) and normalize_name(candidate_name)
    )
    if exact:
        return 2
    fuzzy = any(
        _same_community_name(record_name, candidate_name)
        for record_name in record_names
        for candidate_name in candidate_names
    )
    return 1 if fuzzy else 0


def select_qfang_candidate(
    candidates: Iterable[QfangCandidate], record: CommunityRecord
) -> QfangCandidate | None:
    """仅在行政区和名称都能唯一匹配时选中 Q 房网小区。"""
    matched = [
        (candidate, _candidate_match_score(candidate, record))
        for candidate in candidates
        if _district_matches(candidate, record.administrative_district)
    ]
    matched = [(candidate, score) for candidate, score in matched if score > 0]
    if not matched:
        return None
    highest_score = max(score for _, score in matched)
    best = [candidate for candidate, score in matched if score == highest_score]
    return best[0] if len(best) == 1 else None


def build_qfang_search_url(record: CommunityRecord, query_name: str) -> str:
    """构造按行政区收窄后的 Q 房网小区搜索 URL。"""
    district_path = QFANG_DISTRICT_PATHS.get(_district_key(record.administrative_district))
    path = f"/garden/{district_path}" if district_path else "/garden"
    query = urllib.parse.urlencode({"keyword": query_name})
    return f"{SOURCE_BASE_URL}{path}?{query}"


def _query_names(record: CommunityRecord) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for value in (record.name, *record.aliases):
        name = str(value or "").strip()
        normalized = normalize_name(name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        names.append(name)
    return tuple(names)


def _fetch_html(
    url: str,
    rate_limiter: RequestRateLimiter,
    retries: int,
) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        rate_limiter.wait()
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Q房网请求失败: {last_error}") from last_error


def lookup_qfang_build_year(
    record: CommunityRecord,
    rate_limiter: RequestRateLimiter,
    retries: int,
) -> BackfillResult:
    """查询一个小区，绝不以推荐卡片或模糊多结果作为回填依据。"""
    checked_at = _now()
    query_errors: list[str] = []
    for query_name in _query_names(record):
        search_url = build_qfang_search_url(record, query_name)
        try:
            candidates = parse_qfang_candidates(
                _fetch_html(search_url, rate_limiter, retries)
            )
        except RuntimeError as exc:
            query_errors.append(str(exc))
            continue

        candidate = select_qfang_candidate(candidates, record)
        if candidate is None:
            continue
        status, build_year, rule = choose_build_year(candidate.years)
        if not candidate.years:
            return BackfillResult(
                community_id=record.community_id,
                status=status,
                build_year=build_year,
                candidate_years=(),
                rule=rule,
                evidence=f"{SOURCE_NAME}小区搜索结果未标注建筑年代：{candidate.evidence}",
                source_urls=(candidate.detail_url,),
                checked_at=checked_at,
                message="来源小区已匹配，但未提供可用建成年份",
            )
        return BackfillResult(
            community_id=record.community_id,
            status=status,
            build_year=build_year,
            candidate_years=candidate.years,
            rule=rule,
            evidence=f"{SOURCE_NAME}小区搜索结果：{candidate.evidence}",
            source_urls=(candidate.detail_url,),
            checked_at=checked_at,
            message=None,
        )

    message = (
        "；".join(query_errors[-2:])
        if query_errors
        else "Q房网未找到唯一匹配的小区，保留待后续来源补充"
    )
    return BackfillResult(
        community_id=record.community_id,
        status="PENDING",
        build_year=None,
        candidate_years=(),
        rule="QFANG_NO_UNIQUE_COMMUNITY_MATCH",
        evidence="",
        source_urls=(),
        checked_at=checked_at,
        message=message,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_result_statuses(path: Path) -> dict[int, str]:
    """读取每个小区最后一次检索状态，支持中断后从未处理记录继续。"""
    if not path.exists():
        return {}
    latest_status: dict[int, str] = {}
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            try:
                payload = json.loads(line)
                latest_status[int(payload["community_id"])] = str(payload["status"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
    return latest_status


def append_result(path: Path, result: BackfillResult) -> None:
    """每条立即落盘，保证 SQLite 与 Excel 同步程序可从中断处恢复。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(result.to_json())
        output.write("\n")
        output.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回填深圳小区建成年份（Q房网公开数据）")
    parser.add_argument("--database", type=Path, help="SQLite 路径，默认 persist/community_data.sqlite3")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH, help="JSONL 结果日志路径")
    parser.add_argument("--limit", type=int, help="本次最多处理 N 条，用于分批或验证")
    parser.add_argument("--start-community-id", type=int, default=0, help="跳过不大于该主键的记录")
    parser.add_argument(
        "--request-interval",
        type=float,
        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
        help="相邻 Q 房网请求的最小间隔秒数，默认 1.0",
    )
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="单次请求最多尝试次数")
    parser.add_argument("--retry-manual", action="store_true", help="重新查询已标记人工核对的记录")
    parser.add_argument("--retry-pending", action="store_true", help="重新查询此前未匹配或未找到年份的记录")
    parser.add_argument("--dry-run", action="store_true", help="仅查询和输出结果，不写 SQLite 或 JSONL")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit 必须是正整数")
    if args.request_interval <= 0:
        raise SystemExit("--request-interval 必须大于 0")
    if args.retries <= 0:
        raise SystemExit("--retries 必须是正整数")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    database = CommunityDatabase(args.database)
    result_statuses = load_result_statuses(args.results)
    skipped_result_ids = {
        community_id
        for community_id, status in result_statuses.items()
        if status in {"PENDING", "MANUAL_REVIEW"}
        and not (status == "PENDING" and args.retry_pending)
        and not (status == "MANUAL_REVIEW" and args.retry_manual)
    }
    records = [
        record
        for record in database.list_pending_build_year()
        if record.community_id > args.start_community_id
        and record.community_id not in skipped_result_ids
    ]
    if args.limit is not None:
        records = records[: args.limit]

    log.info(
        "开始回填建成年份: records=%s, skipped_prior=%s, interval=%.2fs",
        len(records),
        len(skipped_result_ids),
        args.request_interval,
    )
    rate_limiter = RequestRateLimiter(args.request_interval)
    counts = {"SUCCESS": 0, "MANUAL_REVIEW": 0, "PENDING": 0}
    for index, record in enumerate(records, start=1):
        result = lookup_qfang_build_year(record, rate_limiter, args.retries)
        if result.status == "SUCCESS" and result.build_year is not None and not args.dry_run:
            database.update_build_year(record.community_id, result.build_year)
        if not args.dry_run:
            append_result(args.results, result)
        counts[result.status] = counts.get(result.status, 0) + 1
        print(result.to_json(), flush=True)
        if index % 25 == 0 or index == len(records):
            log.info("建成年份进度: %s/%s, counts=%s", index, len(records), counts)

    log.info("建成年份回填结束: counts=%s", counts)


if __name__ == "__main__":
    main()
