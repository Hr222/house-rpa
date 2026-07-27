# -*- coding: utf-8 -*-
"""Tests for historical operation-log analysis."""

from pathlib import Path

from app.excel.export_operation_log_excel import (
    InquiryRecord,
    ListingRow,
    derive_output_path,
    finalize_record,
)


def test_log_analysis_deduplicates_rows_per_platform_before_frequency_count():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="weighted_median",
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


def test_log_analysis_multi_peak_uses_lowest_median_without_discount():
    record = InquiryRecord(
        started_at="2026-07-24 00:00:00",
        city="Shenzhen",
        community_name="target",
        area=100.0,
        algorithm_mode="weighted_median",
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
