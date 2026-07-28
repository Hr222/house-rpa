from pathlib import Path
import re

from app.excel.export_operation_log_excel import (
    analyze_evaluation_rows,
    parse_records,
    read_evaluation_rows,
    read_log_lines,
)


root = Path.cwd()
log_lines = read_log_lines(root / "logs" / "20260727-info.log")
records = parse_records(log_lines)
input_paths = [p for p in (root / "results").glob("*112711*.xlsx") if not p.name.startswith("~$")]
evaluation_rows = read_evaluation_rows(min(input_paths, key=lambda p: p.stat().st_size))
analyses = analyze_evaluation_rows(evaluation_rows, records)

prefix_re = re.compile(r"^\S+ \S+ \[\w+\] (?P<logger>[^ ]+) - (?P<msg>.*)$")
range_re = re.compile(
    r"^\[4\] 面积筛选区间: (?P<minimum>[\d.]+)~(?P<maximum>[\d.]+|inf)"
)
range_by_record = []
current = None
for line in log_lines:
    match = prefix_re.match(line)
    if not match:
        continue
    logger = match.group("logger")
    msg = match.group("msg")
    if logger == "app.service" and "查询城市:" in msg:
        current = {}
        range_by_record.append(current)
        continue
    if current is None:
        continue
    area_match = range_re.match(msg)
    if area_match and logger.startswith("app.platforms.adapters."):
        code = logger.rsplit(".", 1)[-1]
        maximum = area_match.group("maximum")
        current[code] = (float(area_match.group("minimum")), float("inf") if maximum == "inf" else float(maximum))

print("records", len(records), "ranges", len(range_by_record))
name_to_code = {"贝壳": "ke", "房天下": "fang", "链家": "lj", "安居客": "ajk", "乐有家": "lyj"}
record_index = {id(record): index for index, record in enumerate(records)}
for analysis in analyses:
    record = analysis.record
    if record is None or not record.deals:
        continue
    ranges = range_by_record[record_index[id(record)]] if record_index[id(record)] < len(range_by_record) else {}
    valid = []
    invalid = []
    for deal in record.deals:
        area_range = ranges.get(name_to_code.get(deal.platform, ""))
        if area_range is None and record.area is not None:
            area_range = (record.area * 0.8, record.area * 1.2)
        is_valid = area_range is not None and deal.area is not None and area_range[0] <= deal.area <= area_range[1]
        (valid if is_valid else invalid).append(deal)
    print(
        analysis.evaluation.row_number,
        analysis.evaluation.community_name,
        f"request={record.area}",
        "ranges=", ranges,
        "valid=", [(d.area, d.price) for d in valid],
        "weak=", [(d.area, d.price) for d in invalid],
    )
