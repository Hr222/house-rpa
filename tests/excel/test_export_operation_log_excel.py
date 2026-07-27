# -*- coding: utf-8 -*-
"""Tests for historical operation-log analysis."""

from app.excel.export_operation_log_excel import (
    InquiryRecord,
    ListingRow,
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
