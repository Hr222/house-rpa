# -*- coding: utf-8 -*-
"""历史操作日志分析测试。"""

from pathlib import Path

from app.excel.export_operation_log_excel import (
    EvaluationRow,
    InquiryRecord,
    ListingRow,
    U_AREA,
    U_COMMUNITY,
    U_QUERY_CITY,
    U_REASON,
    U_STATUS,
    analyze_evaluation_rows,
    build_workbook,
    derive_output_path,
    finalize_record,
    parse_records,
)
from app.utils.listing_dedup import _cross_platform_match, deduplicate_listings


def test_log_analysis_deduplicates_rows_per_platform_before_frequency_count():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "same", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
            ListingRow("Fang", "target", "same", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
            ListingRow("Lianjia", "target", "same", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )

    finalize_record(record)

    assert record.quote_avg == 50000.0
    assert record.final_price == 45000.0
    assert record.success is True


def test_cross_platform_dedup_ignores_title_but_requires_layout():
    rows = [
        ListingRow("Fang", "target", "title-a", 87.62, "3\u5ba41\u5385", 57065.0, 500.0),
        ListingRow("Lianjia", "target", "title-b", 87.62, "3\u5ba41\u5385", 57065.0, 500.0),
        ListingRow("Lyj", "target", "title-c", 87.62, "3\u5ba42\u5385", 57065.0, 500.0),
    ]

    result = deduplicate_listings(rows)

    assert result.raw_count == 3
    assert len(result.same_platform_items) == 3
    assert len(result.items) == 2
    assert len(result.cross_platform_groups) == 1
    assert {item.platform for item in result.cross_platform_groups[0].members} == {
        "Fang",
        "Lianjia",
    }


def test_cross_platform_dedup_resolves_duplicate_candidates_by_exact_title():
    rows = [
        ListingRow(
            "Ke",
            "福安雅园",
            "大四房 户型方正 有入户花园 满五唯一",
            141.04,
            "4室2厅",
            32615.0,
            460.0,
        ),
        ListingRow(
            "Fang",
            "福安雅园",
            "大四房户型方正有入户花园满五唯一",
            141.04,
            "4室2厅",
            32615.0,
            460.0,
        ),
        ListingRow(
            "Fang",
            "福安雅园",
            "深圳龙华福安雅园4室2厅,精装拎包入住,高实用率",
            141.0,
            "4室2厅",
            32624.0,
            460.0,
        ),
        ListingRow(
            "Lianjia",
            "福安雅园",
            "大四房 户型方正 有入户花园 满五唯一",
            141.04,
            "4室2厅",
            32615.0,
            460.0,
        ),
    ]

    result = deduplicate_listings(rows)

    assert len(result.items) == 2
    assert len(result.cross_platform_groups) == 1
    assert {item.platform for item in result.cross_platform_groups[0].members} == {
        "Ke",
        "Fang",
        "Lianjia",
    }


def test_cross_platform_dedup_keeps_exactly_tied_candidates_separate():
    rows = [
        ListingRow("Ke", "target", "title-a", 100.0, "3室2厅", 50000.0, 500.0),
        ListingRow("Fang", "target", "title-b", 100.0, "3室2厅", 50000.0, 500.0),
        ListingRow("Fang", "target", "title-c", 100.0, "3室2厅", 50000.0, 500.0),
        ListingRow("Lianjia", "target", "title-d", 100.0, "3室2厅", 50000.0, 500.0),
    ]

    result = deduplicate_listings(rows)

    assert len(result.items) == 3
    assert len(result.cross_platform_groups) == 1


def test_cross_platform_dedup_uses_same_title_when_both_layouts_are_missing():
    rows = [
        ListingRow("Fang", "target", "same title", 100.0, "", 50000.0, 500.0),
        ListingRow("Lianjia", "target", "same title", 100.0, "", 50000.0, 500.0),
        ListingRow("Lyj", "target", "different title", 100.0, "", 50000.0, 500.0),
    ]

    result = deduplicate_listings(rows)

    assert len(result.items) == 2
    assert len(result.cross_platform_groups) == 1
    assert "\u6237\u578b\u6709\u7f3a\u5931" in result.cross_platform_groups[0].reason


def test_cross_platform_dedup_title_contains_core_title_when_one_layout_is_missing():
    rows = [
        ListingRow("Fang", "target", "\u597d\u6237\u578b", 50.0, "1\u623f1\u5385", 3000.0, 15.0),
        ListingRow(
            "Lianjia",
            "target",
            "\u597d\u6237\u578b\u554a\uff0c\u4e94\u5e74\u56de\u672c",
            50.0,
            "1\u623f1\u5385",
            3000.0,
            15.0,
        ),
        ListingRow("Lyj", "target", "\u597d\u6237\u578b", 50.0, "", 3000.0, 15.0),
    ]

    result = deduplicate_listings(rows)

    assert _cross_platform_match(
        rows[2], rows[0], area_tolerance=0.5, unit_price_tolerance=100.0
    )
    assert not _cross_platform_match(
        rows[2], rows[1], area_tolerance=0.5, unit_price_tolerance=100.0
    )
    assert len(result.items) == 1
    assert len(result.cross_platform_groups) == 1
    assert "\u4ee3\u8868\u623f\u6e90" in result.cross_platform_groups[0].reason


def test_workbook_marks_removed_duplicate_rows_in_gray_italic():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "title-a", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
            ListingRow("Lianjia", "target", "title-b", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )

    workbook = build_workbook([record])
    sheet = workbook["target"]

    assert sheet["J13"].value == "\u53bb\u91cd\u72b6\u6001"
    assert sheet["J14"].value == "\u4fdd\u7559\u7edf\u8ba1"
    assert "\u8de8\u5e73\u53f0\u91cd\u590d" in sheet["J15"].value
    assert sheet["J15"].font.italic is True
    assert sheet["J15"].fill.fgColor.rgb.endswith("E7E6E6")


def test_workbook_does_not_freeze_panes_and_wraps_detail_text():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="DEFAULT",
        branch_text="line one\nline two",
        listings=[
            ListingRow("Fang", "target", "long title", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )

    workbook = build_workbook([record])
    sheet = workbook["target"]

    assert sheet.freeze_panes is None
    assert sheet["B10"].alignment.wrap_text is True


def test_analysis_summary_does_not_freeze_panes_and_wraps_text():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "listing", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )
    finalize_record(record)
    analysis_rows = analyze_evaluation_rows(
        [EvaluationRow(2, "Shenzhen", 100.0, "target", 50000.0)],
        [record],
    )

    workbook = build_workbook([record], analysis_rows)
    sheet = workbook["分析汇总"]

    assert sheet.freeze_panes is None
    assert sheet["J6"].alignment.wrap_text is True


def test_analysis_summary_uses_the_current_nineteen_column_layout():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "listing", 100.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )
    finalize_record(record)
    analysis_rows = analyze_evaluation_rows(
        [EvaluationRow(2, "Shenzhen", 100.0, "target", 50000.0)],
        [record],
    )

    workbook = build_workbook([record], analysis_rows)
    sheet = workbook["分析汇总"]

    assert sheet.max_column == 19
    assert [sheet.cell(row=5, column=column).value for column in range(1, 20)] == [
        "行号",
        "城市",
        "请求面积(㎡)",
        "小区",
        "评估单价",
        "最终取值",
        "最终偏差",
        "决策分支",
        "所有候选峰（频率）",
        "分析结论",
        "评价口径",
        "原始房源数",
        "同平台去重后",
        "跨平台去重后",
        "跨平台重复组",
        "严格±1㎡候选",
        "弱参考信息",
        "严格范围房源数",
        "弱参考补充数",
    ]


def test_log_analysis_multi_peak_uses_lowest_median_without_discount():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "low", 100.0, "3 rooms 2 halls", 100000.0, 1000.0),
            ListingRow("Fang", "target", "mid", 100.0, "3 rooms 2 halls", 150000.0, 1500.0),
            ListingRow("Fang", "target", "high", 100.0, "3 rooms 2 halls", 200000.0, 2000.0),
        ] * 6,
    )

    finalize_record(record)

    assert record.quote_avg == 100000.0
    assert record.final_price == 100000.0
    assert record.branch_code == "WEIGHTED_MEDIAN_MULTI"


def test_log_analysis_combines_logged_target_area_deal_result():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=80.0,
        algorithm_mode="DEFAULT",
        deal_avg=40000.0,
        listings=[
            ListingRow("Fang", "target", "listing", 80.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )

    finalize_record(record)

    assert record.quote_avg == 50000.0
    assert record.deal_avg == 40000.0
    assert record.final_price == 45000.0
    assert record.branch_code == "WEIGHTED_MEDIAN_COMBINED"


def test_log_analysis_without_target_area_deal_keeps_listing_result():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=80.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "listing", 80.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )

    finalize_record(record)

    assert record.final_price == 45000.0
    assert record.branch_code == "WEIGHTED_MEDIAN"


def test_log_analysis_rebuilds_logged_single_peak_with_deal_without_discount():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=80.0,
        algorithm_mode="DEFAULT",
        deal_avg=40000.0,
        final_price=42500.0,
        final_price_logged=True,
        branch_code="WEIGHTED_MEDIAN_COMBINED",
        branch_code_logged=True,
        listings=[
            ListingRow("Fang", "target", "listing", 80.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )

    finalize_record(record)

    assert record.final_price == 45000.0
    assert record.branch_code == "WEIGHTED_MEDIAN_COMBINED"


def test_analysis_treats_single_peak_without_deal_discount_as_rule_handling():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=80.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "listing", 80.0, "3 rooms 2 halls", 50000.0, 500.0),
        ],
    )
    finalize_record(record)

    rows = analyze_evaluation_rows(
        [EvaluationRow(2, "Shenzhen", 80.0, "target", 55000.0)],
        [record],
    )

    assert rows[0].conclusion == "无真实成交价，按规则九折"
    assert rows[0].evaluation_scope == "计入单峰评价"


def test_analysis_does_not_count_platform_weak_reference_when_final_peak_does_not_use_it():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "target", "strict-1", 100.0, "3 rooms 2 halls", 20000.0, 200.0),
            ListingRow("Fang", "target", "strict-2", 100.0, "3 rooms 2 halls", 20000.0, 200.0),
            ListingRow("Fang", "target", "strict-3", 100.0, "3 rooms 2 halls", 20000.0, 200.0),
            ListingRow("Fang", "target", "weak-1", 110.0, "3 rooms 2 halls", 40000.0, 400.0),
        ],
        platform_notes={
            "Fang": {
                "status": "SUCCESS",
                "reference_code": "WEAK_AREA_REFERENCE",
                "reference_area_tolerance": "10.00",
                "reference_area_min": "90.00",
                "reference_area_max": "110.00",
                "reference_listing_count": "1",
            }
        },
    )
    finalize_record(record)

    rows = analyze_evaluation_rows(
        [EvaluationRow(2, "Shenzhen", 100.0, "target", 20000.0)],
        [record],
    )

    assert rows[0].weak_reference_text == ""
    assert rows[0].weak_listing_count == 0


def test_analysis_output_uses_evaluation_workbook_stem_under_results():
    output = derive_output_path(
        Path("logs/20260724-info.log"),
        None,
        Path("results/评估对比_20260724_173024.xlsx"),
    )

    assert output.name == "评估对比_20260724_173024_分析.xlsx"
    assert output.parent.name == "results"


def test_analysis_output_falls_back_to_log_stem_under_results():
    output = derive_output_path(Path("logs/20260724-info.log"), None)

    assert output.name == "20260724-info_分析.xlsx"
    assert output.parent.name == "results"


def test_analysis_summary_explains_multi_peak_and_selected_lowest_peak():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="深圳",
        community_name="御景水岸",
        area=354.97,
        algorithm_mode="DEFAULT",
        listings=[
            ListingRow("Fang", "御景水岸", "low", 354.97, "", 126836.0, 0.0),
        ] * 3
        + [
            ListingRow("Fang", "御景水岸", "high", 354.97, "", 155634.0, 0.0),
        ] * 2,
    )
    finalize_record(record)

    analysis_rows = analyze_evaluation_rows(
        [EvaluationRow(2, "深圳", 354.97, "御景水岸", 72000.0)],
        [record],
    )
    workbook = build_workbook([record], analysis_rows)
    sheet = workbook["分析汇总"]

    assert record.final_price == 126836.0
    assert analysis_rows[0].conclusion == "原始数据/评估基准不匹配，排除评价"
    assert sheet["F6"].value == 126836.0
    assert "155,634" in sheet["I6"].value
    assert sheet["J6"].value == "原始数据/评估基准不匹配，排除评价"


def test_log_analysis_preserves_weak_reference_metadata():
    lines = [
        "2026-07-27 12:00:00 [INFO] app.service - 查询城市: 深圳, 小区: 示例花园, 面积: 100.0㎡",
        "2026-07-27 12:00:01 [INFO] app.service - 乐有家: {小区名称: 示例花园, 标题: 房源1, 面积: 100.0平米, 几房几厅: 3房2厅, 售价: 50000元/平, 总价: 500万}",
        "2026-07-27 12:00:01 [INFO] app.service - 乐有家: {小区名称: 示例花园, 标题: 房源2, 面积: 102.0平米, 几房几厅: 3房2厅, 售价: 50000元/平, 总价: 510万}",
        "2026-07-27 12:00:01 [INFO] app.service - 乐有家: {小区名称: 示例花园, 标题: 房源3, 面积: 103.0平米, 几房几厅: 3房2厅, 售价: 50000元/平, 总价: 515万}",
        "2026-07-27 12:00:01 [INFO] app.service - 乐有家弱参考: referenceCode=WEAK_AREA_REFERENCE referenceAreaTolerance=3.00 referenceAreaMin=97.00 referenceAreaMax=103.00 referenceListingCount=2",
        "2026-07-27 12:00:02 [INFO] app.service - 在售均价(单位:元/平): 50000",
        "2026-07-27 12:00:02 [INFO] app.service - 成交均价(单位:元/平): None",
        "2026-07-27 12:00:02 [INFO] app.service - 最终取值(单位:元/平): 45000",
    ]

    lines.insert(
        -3,
        "2026-07-27 12:00:02 [INFO] app.service - "
        "finalWeakReference: referenceCode=WEAK_AREA_REFERENCE "
        "referenceAreaTolerance=3.00 referenceAreaMin=97.00 "
        "referenceAreaMax=103.00 referenceListingCount=2",
    )
    lines.insert(
        -3,
        "2026-07-27 12:00:02 [INFO] app.service - "
        "finalBranch: branchCode=WEIGHTED_MEDIAN",
    )
    records = parse_records(lines)

    assert len(records) == 1
    note = records[0].platform_notes["乐有家"]
    assert note["reference_code"] == "WEAK_AREA_REFERENCE"
    assert note["reference_area_tolerance"] == "3.00"
    assert note["reference_listing_count"] == "2"

    analysis_rows = analyze_evaluation_rows(
        [EvaluationRow(2, "深圳", 100.0, "示例花园", 50000.0)],
        records,
    )
    workbook = build_workbook(records, analysis_rows)
    sheet = workbook["分析汇总"]
    assert "WEAK_AREA_REFERENCE" in sheet["Q6"].value
    assert sheet["R6"].value == 1
    assert sheet["S6"].value == 2


def test_log_parser_reconstructs_house_id_and_negative_weak_area_min():
    lines = [
        "2026-07-27 12:00:00 [INFO] app.service - "
        f"{U_QUERY_CITY}: Shenzhen, {U_COMMUNITY}: target, {U_AREA}: 1.0㎡",
        "2026-07-27 12:00:01 [INFO] app.service - "
        "PlatformA: {小区名称: target, 标题: listing, 面积: 1.0平米, "
        "几房几厅: 1房1厅, 售价: 50000元/平, 总价: 5万, 房源编号: house-1}",
        "2026-07-27 12:00:01 [INFO] app.service - "
        "finalWeakReference: referenceCode=WEAK_AREA_REFERENCE "
        "referenceAreaTolerance=20.00 referenceAreaMin=-19.00 "
        "referenceAreaMax=21.00 referenceListingCount=1",
        "2026-07-27 12:00:01 [INFO] app.service - finalBranch: branchCode=WEIGHTED_MEDIAN",
    ]

    record = parse_records(lines)[0]

    assert record.listings[0].house_id == "house-1"
    assert record.reference_area_min == "-19.00"
    assert record.reference_area_max == "21.00"


def test_log_parser_preserves_final_no_data_branch_codes():
    for branch in ("NO_DATA", "NO_MATCHING_AREA"):
        lines = [
            "2026-07-27 12:00:00 [INFO] app.service - "
            f"{U_QUERY_CITY}: Shenzhen, {U_COMMUNITY}: target, {U_AREA}: 100.0㎡",
            "2026-07-27 12:00:01 [INFO] app.service - "
            f"PlatformA: {{{U_STATUS}: {branch}, {U_REASON}: no usable listings}}",
            f"2026-07-27 12:00:01 [INFO] app.service - finalBranch: branchCode={branch}",
        ]

        record = parse_records(lines)[0]
        assert record.branch_code == branch
        assert record.branch_code_logged is True
        assert record.success is False
        assert record.platform_notes["PlatformA"]["status"] == branch
