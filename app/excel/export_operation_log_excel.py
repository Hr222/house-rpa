# -*- coding: utf-8 -*-
"""Export attached inquiry logs to an Excel workbook for debugging analysis."""

from __future__ import annotations

import argparse
import ast
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

U_QUERY_CITY = "\u67e5\u8be2\u57ce\u5e02"
U_COMMUNITY = "\u5c0f\u533a"
U_AREA = "\u9762\u79ef"
U_QM = "\u33a1"
U_LISTING_COMMUNITY = "\u5c0f\u533a\u540d\u79f0"
U_TITLE = "\u6807\u9898"
U_LAYOUT = "\u51e0\u623f\u51e0\u5385"
U_SELL = "\u552e\u4ef7"
U_TOTAL = "\u603b\u4ef7"
U_STATUS = "\u72b6\u6001"
U_REASON = "\u539f\u56e0"
U_DEAL = "\u6210\u4ea4"
U_DATE = "\u65e5\u671f"
U_COMPLETED = "\u91c7\u96c6\u5b8c\u6210"
U_QUOTE_AVG = "\u5728\u552e\u5747\u4ef7"
U_DEAL_AVG = "\u6210\u4ea4\u5747\u4ef7"
U_FINAL_PRICE = "\u6700\u7ec8\u53d6\u503c"
U_SUCCESS = "\u6210\u529f"
U_LISTING_SECTION = "\u5728\u552e\u623f\u6e90\u660e\u7ec6"
U_DEAL_SECTION = "\u6210\u4ea4\u660e\u7ec6\u4e0e\u8bf4\u660e"
U_PRICE_UNIT = "\u5143/\u5e73"
U_PRICE_UNIT_M2 = "\u5143/\u33a1"
U_WAN = "\u4e07"
U_NONE = "\u672a\u91c7\u96c6\u5230"
U_RAW = "\u539f\u59cb\u8bf4\u660e"
U_DEAL_RECORD = "\u6210\u4ea4\u8bb0\u5f55"
U_DEAL_NOTE = "\u8bf4\u660e"
U_PLATFORM = "\u5e73\u53f0"
U_CITY = "\u57ce\u5e02"
U_REQUEST_AREA = "\u8bf7\u6c42\u9762\u79ef(\u33a1)"
U_REQUEST_ID = "requestId"
U_ALGO = "algorithmMode"
U_STARTED = "startedAt"
U_FINISHED = "finishedAt"
U_ELAPSED = "elapsedSeconds"
U_BRANCH = "branchCode"
U_BRANCH_TEXT = "branch"
U_QUOTE = "quoteAvg"
U_DEALV = "dealAvg"
U_FINAL = "finalPrice"
U_NOTE = "\u5907\u6ce8"
U_UNIT_LABEL = "\u5355\u4f4d:\u5143/\u5e73"
U_ELAPSED_WORD = "\u8017\u65f6"
U_SECONDS = "\u79d2"
U_PINGMI = "\u5e73\u7c73"
U_DANJIA = "\u5355\u4ef7"
U_WU_L = "\u65e0\uff08"
U_RPAREN = "\uff09"
U_TOTAL_PREFIX = "\uff08\u5171"
U_TOTAL_SUFFIX = "\u6761\uff09"
U_LOG_INCOMPLETE = "\u65e5\u5fd7\u4e2d\u672a\u89e3\u6790\u5230\u5b8c\u6574\u7ed3\u679c"
U_STATUS_SUCCESS = "SUCCESS"
U_PINGMI_LABEL = "\u5e73\u7c73"
U_FAILED = "FAILED"
U_QUOTE_ONLY = "QUOTE_ONLY"
U_QUOTE_DISCOUNT = "QUOTE_DISCOUNT"
U_DEAL_ONLY = "DEAL_ONLY"
U_TAKE_LOWER = "TAKE_LOWER"

ENCODINGS = ("utf-8", "utf-8-sig", "gb18030")
INVALID_SHEET_CHARS = set('[]:*?/\\')


@dataclass
class ListingRow:
    platform: str
    community_name: str
    title: str
    area: Optional[float]
    layout: str
    unit_price: Optional[float]
    total_price: Optional[float]


@dataclass
class DealRow:
    platform: str
    area: Optional[float]
    date: str
    total_price: Optional[float]
    price: Optional[float]


@dataclass
class InquiryRecord:
    started_at: str
    city: str
    community_name: str
    area: Optional[float]
    request_id: Optional[str] = None
    algorithm_mode: str = "default"
    elapsed_seconds: Optional[float] = None
    finished_at: Optional[str] = None
    quote_avg: Optional[float] = None
    deal_avg: Optional[float] = None
    final_price: Optional[float] = None
    success: bool = False
    branch_code: Optional[str] = None
    branch_text: Optional[str] = None
    listings: list[ListingRow] = field(default_factory=list)
    deals: list[DealRow] = field(default_factory=list)
    platform_notes: dict[str, dict[str, str]] = field(default_factory=dict)


PREFIX_RE = re.compile(r"^(?P<ts>\S+ \S+) \[(?P<level>\w+)\] (?P<logger>[^ ]+) - (?P<msg>.*)$")
START_RE = re.compile(
    rf"^{re.escape(U_QUERY_CITY)}: (?P<city>.*?), {re.escape(U_COMMUNITY)}: (?P<community>.*?), "
    rf"{re.escape(U_AREA)}: (?P<area>[\d.]+){re.escape(U_QM)}$"
)
SUMMARY_RE = re.compile(
    rf"^(?P<label>{re.escape(U_QUOTE_AVG)}|{re.escape(U_DEAL_AVG)}|{re.escape(U_FINAL_PRICE)})"
    rf"\({re.escape(U_UNIT_LABEL)}\): (?P<value>.+)$"
)
RUNTIME_RE = re.compile(
    rf"^\[{re.escape(U_COMPLETED)}\] request=(?P<request>\{{.*\}}) "
    rf"{re.escape(U_ELAPSED_WORD)}=(?P<elapsed>[\d.]+){re.escape(U_SECONDS)}"
)
LISTING_RE = re.compile(
    r"^(?P<platform>[^:]+): "
    r"\{" + re.escape(U_LISTING_COMMUNITY) + r": (?P<community_name>.*?), "
    + re.escape(U_TITLE) + r": (?P<title>.*?), "
    + re.escape(U_AREA) + r": (?P<area>.*?)" + re.escape(U_PINGMI) + r", "
    + re.escape(U_LAYOUT) + r": (?P<layout>.*?), "
    + re.escape(U_SELL) + r": (?P<unit_price>.*?)" + re.escape(U_PRICE_UNIT) + r", "
    + re.escape(U_TOTAL) + r": (?P<total_price>.*?)" + re.escape(U_WAN) + r"\}$"
)
NO_DATA_RE = re.compile(
    rf"^(?P<platform>[^:]+): \{{{re.escape(U_STATUS)}: (?P<status>.*?), {re.escape(U_REASON)}: (?P<reason>.*)\}}$"
)
DEAL_RECORD_RE = re.compile(
    r"^(?P<platform>.+?)" + re.escape(U_DEAL) + r": "
    r"\{" + re.escape(U_AREA) + r": (?P<area>.*?)" + re.escape(U_QM) + r", "
    + re.escape(U_DATE) + r": (?P<date>.*?), "
    + re.escape(U_TOTAL) + r": (?P<total_price>.*?)" + re.escape(U_WAN) + r", "
    + re.escape(U_DANJIA) + r": (?P<price>.*?)" + re.escape(U_PRICE_UNIT) + r"\}$"
)
DEAL_SOURCE_RE = re.compile(
    r"^(?P<platform>.+?)" + re.escape(U_DEAL) + r": "
    + re.escape(U_WU_L) + r"(?P<source>.*?)(?: (?P<price>.*?))?"
    + re.escape(U_PRICE_UNIT_M2) + re.escape(U_RPAREN) + r"$"
)
DEAL_NONE_RE = re.compile(r"^(?P<platform>.+?)" + re.escape(U_DEAL) + r": " + re.escape(U_NONE) + r"$")
DEAL_LIST_RE = re.compile(
    r"^(?P<platform>.+?)" + re.escape(U_DEAL) + r": \[(?P<prices>.*?)\]"
    + re.escape(U_TOTAL_PREFIX) + r"(?P<count>\d+)" + re.escape(U_TOTAL_SUFFIX) + r"$"
)


def read_log_lines(path: Path) -> list[str]:
    last_exc: Optional[Exception] = None
    for encoding in ENCODINGS:
        try:
            return path.read_text(encoding=encoding).splitlines()
        except Exception as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def clean_text(value: Optional[str]) -> str:
    return "" if value is None else value.strip()


def to_float(value: Optional[str]) -> Optional[float]:
    text = clean_text(value).replace(",", "")
    if not text or text in {"-", "\u2014", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def infer_branch(quote_avg: Optional[float], deal_avg: Optional[float], algorithm_mode: str) -> tuple[str, str]:
    if algorithm_mode == "quote_only":
        if quote_avg is None:
            return U_FAILED, "\u65e0\u5728\u552e\u6570\u636e"
        return U_QUOTE_ONLY, "\u4ec5\u6309\u5728\u552e\u5747\u4ef7\u6253\u6298"
    if quote_avg is None and deal_avg is None:
        return U_FAILED, "\u65e0\u53ef\u7528\u7ed3\u679c"
    if deal_avg is None:
        return U_QUOTE_DISCOUNT, "\u6210\u4ea4\u7f3a\u5931\uff0c\u5728\u552e\u5747\u4ef7\u6253\u6298"
    if quote_avg is None:
        return U_DEAL_ONLY, "\u4ec5\u53d6\u6210\u4ea4\u5747\u4ef7"
    diff = abs(quote_avg - deal_avg) / deal_avg if deal_avg else 999
    if diff <= 0.10:
        return U_TAKE_LOWER, "\u5dee\u5f02\u5728\u9608\u503c\u5185\uff0c\u53d6\u8f83\u4f4e\u503c"
    return U_DEAL_ONLY, "\u5dee\u5f02\u8d85\u9608\u503c\uff0c\u53ea\u53d6\u6210\u4ea4\u5747\u4ef7"


def finalize_record(record: InquiryRecord) -> None:
    record.success = record.final_price is not None
    record.branch_code, record.branch_text = infer_branch(record.quote_avg, record.deal_avg, record.algorithm_mode)


def parse_records(lines: list[str]) -> list[InquiryRecord]:
    records: list[InquiryRecord] = []
    current: Optional[InquiryRecord] = None

    for line in lines:
        prefix_match = PREFIX_RE.match(line)
        if not prefix_match:
            continue
        ts = prefix_match.group("ts")
        logger = prefix_match.group("logger")
        msg = prefix_match.group("msg")

        if logger == "app.service":
            start_match = START_RE.match(msg)
            if start_match:
                if current is not None:
                    finalize_record(current)
                    records.append(current)
                current = InquiryRecord(
                    started_at=ts,
                    city=clean_text(start_match.group("city")),
                    community_name=clean_text(start_match.group("community")),
                    area=to_float(start_match.group("area")),
                )
                continue

        if current is None:
            continue

        if logger == "app.service":
            listing_match = LISTING_RE.match(msg)
            if listing_match:
                platform = clean_text(listing_match.group("platform"))
                current.listings.append(
                    ListingRow(
                        platform=platform,
                        community_name=clean_text(listing_match.group("community_name")),
                        title=clean_text(listing_match.group("title")),
                        area=to_float(listing_match.group("area")),
                        layout=clean_text(listing_match.group("layout")),
                        unit_price=to_float(listing_match.group("unit_price")),
                        total_price=to_float(listing_match.group("total_price")),
                    )
                )
                current.platform_notes.setdefault(platform, {"status": U_STATUS_SUCCESS})
                continue

            no_data_match = NO_DATA_RE.match(msg)
            if no_data_match:
                current.platform_notes[clean_text(no_data_match.group("platform"))] = {
                    "status": clean_text(no_data_match.group("status")),
                    "reason": clean_text(no_data_match.group("reason")),
                }
                continue

            deal_record_match = DEAL_RECORD_RE.match(msg)
            if deal_record_match:
                platform = clean_text(deal_record_match.group("platform"))
                current.deals.append(
                    DealRow(
                        platform=platform,
                        area=to_float(deal_record_match.group("area")),
                        date=clean_text(deal_record_match.group("date")),
                        total_price=to_float(deal_record_match.group("total_price")),
                        price=to_float(deal_record_match.group("price")),
                    )
                )
                current.platform_notes.setdefault(platform, {"status": U_STATUS_SUCCESS})
                continue

            deal_source_match = DEAL_SOURCE_RE.match(msg)
            if deal_source_match:
                platform = clean_text(deal_source_match.group("platform"))
                current.platform_notes.setdefault(platform, {"status": U_STATUS_SUCCESS})
                current.platform_notes[platform].update(
                    {
                        "deal_source": clean_text(deal_source_match.group("source")),
                        "deal_price": clean_text(deal_source_match.group("price")),
                    }
                )
                continue

            deal_none_match = DEAL_NONE_RE.match(msg)
            if deal_none_match:
                platform = clean_text(deal_none_match.group("platform"))
                current.platform_notes.setdefault(platform, {"status": U_STATUS_SUCCESS})
                current.platform_notes[platform]["deal_note"] = U_NONE
                continue

            deal_list_match = DEAL_LIST_RE.match(msg)
            if deal_list_match:
                platform = clean_text(deal_list_match.group("platform"))
                current.platform_notes.setdefault(platform, {"status": U_STATUS_SUCCESS})
                current.platform_notes[platform].update(
                    {
                        "deal_prices_text": clean_text(deal_list_match.group("prices")),
                        "deal_count": clean_text(deal_list_match.group("count")),
                    }
                )
                continue

            summary_match = SUMMARY_RE.match(msg)
            if summary_match:
                value = to_float(summary_match.group("value"))
                label = summary_match.group("label")
                if label == U_QUOTE_AVG:
                    current.quote_avg = value
                elif label == U_DEAL_AVG:
                    current.deal_avg = value
                else:
                    current.final_price = value
                continue

        if logger == "app.runtime":
            runtime_match = RUNTIME_RE.match(msg)
            if runtime_match:
                current.finished_at = ts
                current.elapsed_seconds = to_float(runtime_match.group("elapsed"))
                request = ast.literal_eval(runtime_match.group("request"))
                current.request_id = request.get("requestId")
                current.algorithm_mode = request.get("algorithmMode", "default")
                continue

    if current is not None:
        finalize_record(current)
        records.append(current)

    return records


def safe_sheet_name(name: str, used_names: Counter[str]) -> str:
    cleaned = "".join("_" if ch in INVALID_SHEET_CHARS else ch for ch in (name or "Sheet"))
    cleaned = cleaned[:31] or "Sheet"
    used_names[cleaned] += 1
    if used_names[cleaned] == 1:
        return cleaned
    suffix = f"_{used_names[cleaned]}"
    return cleaned[: 31 - len(suffix)] + suffix


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def build_workbook(records: list[InquiryRecord]) -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)

    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill_header = PatternFill("solid", fgColor="D9EAF7")
    fill_title = PatternFill("solid", fgColor="A9D18E")
    fill_section = PatternFill("solid", fgColor="F4B183")
    font_title = Font(bold=True, size=14)
    font_header = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="top", wrap_text=True)
    used_sheet_names: Counter[str] = Counter()

    def write_row(ws, row_index: int, values: list[object], header: bool = False) -> None:
        for col_index, value in enumerate(values, 1):
            cell = ws.cell(row=row_index, column=col_index, value=value)
            if header:
                cell.fill = fill_header
                cell.font = font_header
                cell.alignment = center
            else:
                cell.alignment = left
            cell.border = border

    for record in records:
        ws = workbook.create_sheet(title=safe_sheet_name(record.community_name, used_sheet_names))
        ws.merge_cells("A1:I1")
        title = ws["A1"]
        title.value = record.community_name
        title.font = font_title
        title.fill = fill_title
        title.alignment = center
        title.border = border

        write_row(ws, 3, [U_CITY, U_REQUEST_AREA, U_REQUEST_ID, U_ALGO], header=True)
        write_row(ws, 4, [record.city, record.area, record.request_id or "", record.algorithm_mode or ""])
        write_row(ws, 5, [U_STARTED, U_FINISHED, U_ELAPSED, U_SUCCESS], header=True)
        write_row(ws, 6, [record.started_at, record.finished_at or "", record.elapsed_seconds, record.success])
        write_row(ws, 7, [U_QUOTE, U_DEALV, U_FINAL, U_BRANCH], header=True)
        write_row(ws, 8, [record.quote_avg, record.deal_avg, record.final_price, record.branch_code or ""])
        write_row(ws, 9, [U_BRANCH_TEXT, U_NOTE, "", ""], header=True)
        write_row(ws, 10, [record.branch_text or "", "" if record.success else U_LOG_INCOMPLETE, "", ""])

        ws.merge_cells("A12:I12")
        listing_section = ws["A12"]
        listing_section.value = U_LISTING_SECTION
        listing_section.font = font_header
        listing_section.fill = fill_section
        listing_section.alignment = left
        listing_section.border = border

        write_row(
            ws,
            13,
            [
                U_PLATFORM,
                U_STATUS,
                U_REASON,
                U_LISTING_COMMUNITY,
                U_TITLE,
                f"{U_AREA}({U_PINGMI_LABEL})",
                U_LAYOUT,
                f"{U_SELL}({U_PRICE_UNIT})",
                f"{U_TOTAL}({U_WAN})",
            ],
            header=True,
        )

        grouped_listings: dict[str, list[ListingRow]] = defaultdict(list)
        for listing in record.listings:
            grouped_listings[listing.platform].append(listing)

        platforms = sorted(set(grouped_listings) | set(record.platform_notes))
        if not platforms:
            platforms = [""]

        row_index = 14
        for platform in platforms:
            note = record.platform_notes.get(platform, {})
            rows = grouped_listings.get(platform, [])
            if rows:
                for listing in rows:
                    write_row(
                        ws,
                        row_index,
                        [
                            platform,
                            note.get("status", U_STATUS_SUCCESS),
                            note.get("reason", ""),
                            listing.community_name,
                            listing.title,
                            listing.area,
                            listing.layout,
                            listing.unit_price,
                            listing.total_price,
                        ],
                    )
                    row_index += 1
            else:
                write_row(ws, row_index, [platform, note.get("status", ""), note.get("reason", ""), "", "", "", "", "", ""])
                row_index += 1

        row_index += 1
        ws.merge_cells(start_row=row_index, start_column=1, end_row=row_index, end_column=7)
        deal_section = ws.cell(row=row_index, column=1, value=U_DEAL_SECTION)
        deal_section.font = font_header
        deal_section.fill = fill_section
        deal_section.alignment = left
        deal_section.border = border
        row_index += 1

        write_row(
            ws,
            row_index,
            [
                U_PLATFORM,
                U_STATUS,
                f"{U_AREA}({U_QM})",
                U_DATE,
                f"{U_TOTAL}({U_WAN})",
                f"{U_DANJIA}({U_PRICE_UNIT})",
                U_RAW,
            ],
            header=True,
        )
        row_index += 1

        deal_rows = 0
        for deal in record.deals:
            write_row(ws, row_index, [deal.platform, U_DEAL_RECORD, deal.area, deal.date, deal.total_price, deal.price, ""])
            row_index += 1
            deal_rows += 1

        for platform in platforms:
            note = record.platform_notes.get(platform, {})
            raw_parts = []
            if note.get("deal_source"):
                raw_parts.append(note["deal_source"])
                if note.get("deal_price"):
                    raw_parts.append(note["deal_price"])
            if note.get("deal_note"):
                raw_parts.append(note["deal_note"])
            if note.get("deal_prices_text"):
                raw_parts.append(note["deal_prices_text"])
            if raw_parts:
                write_row(ws, row_index, [platform, U_DEAL_NOTE, "", "", "", "", " / ".join(raw_parts)])
                row_index += 1
                deal_rows += 1

        if deal_rows == 0:
            write_row(ws, row_index, ["", U_DEAL_NOTE, "", "", "", "", U_NONE])
            row_index += 1

        ws.freeze_panes = "A13"
        for col_index, width in {1: 12, 2: 12, 3: 18, 4: 14, 5: 52, 6: 12, 7: 14, 8: 14, 9: 12}.items():
            ws.column_dimensions[get_column_letter(col_index)].width = width

    return workbook


def derive_output_path(log_path: Path, output_path: Optional[Path]) -> Path:
    if output_path is not None:
        return output_path
    return log_path.with_name("评估对比_20260723_165310_详细数据.xlsx")


def save_workbook(workbook: Workbook, path: Path) -> Path:
    try:
        workbook.save(path)
        return path
    except PermissionError:
        fallback = next_available_path(path)
        workbook.save(fallback)
        return fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", type=Path, help="Path to an inquiry log file")
    parser.add_argument("-o", "--output", type=Path, help="Output xlsx path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lines = read_log_lines(args.log_path)
    records = parse_records(lines)
    if not records:
        raise SystemExit("No inquiry records parsed from log.")
    workbook = build_workbook(records)
    output_path = derive_output_path(args.log_path, args.output)
    actual_path = save_workbook(workbook, output_path)
    complete_count = sum(1 for record in records if record.final_price is not None)
    incomplete_count = len(records) - complete_count
    total_listings = sum(len(record.listings) for record in records)
    total_deals = sum(len(record.deals) for record in records)
    print(f"saved_to={actual_path}")
    print(f"records={len(records)}")
    print(f"complete={complete_count}")
    print(f"incomplete={incomplete_count}")
    print(f"listings={total_listings}")
    print(f"deals={total_deals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
