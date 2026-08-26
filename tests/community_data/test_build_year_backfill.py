# -*- coding: utf-8 -*-
"""建成年份批处理规则测试。"""

from app.community_data.models import CommunityRecord
from scripts.community_data.backfill_build_year import (
    QfangCandidate,
    build_qfang_search_url,
    choose_build_year,
    load_result_statuses,
    parse_qfang_years,
    select_qfang_candidate,
)
from scripts.community_data.reset_qfang_build_years import load_qfang_success_ids
from scripts.community_data.aggregate_fang_build_year import aggregate_fang_results


def make_record(administrative_district: str) -> CommunityRecord:
    return CommunityRecord(
        community_id=1,
        community_group_id=1,
        city="深圳",
        administrative_district=administrative_district,
        district=None,
        name="华侨新村",
        phase=None,
        build_year=None,
        aliases=(),
        address=f"深圳市{administrative_district}华侨新村",
        longitude=None,
        latitude=None,
        coordinate_system=None,
        geocode_status="PENDING",
        geocode_level=None,
        geocode_reliability=None,
        remark=None,
        created_at="2026-08-25T00:00:00+00:00",
        updated_at="2026-08-25T00:00:00+00:00",
    )


def test_parse_qfang_year_range() -> None:
    assert parse_qfang_years("建筑年代 2003年-2004年建") == (2003, 2004)


def test_latest_within_one_year_is_success() -> None:
    assert choose_build_year((2003, 2004)) == (
        "SUCCESS",
        2004,
        "QFANG_LATEST_WITHIN_1_YEAR",
    )


def test_year_span_over_one_requires_manual_review() -> None:
    status, year, rule = choose_build_year((2003, 2006))
    assert (status, year, rule) == (
        "MANUAL_REVIEW",
        None,
        "QFANG_REPORTED_YEAR_RANGE_OVER_1",
    )


def test_same_name_in_another_administrative_district_is_not_selected() -> None:
    record = make_record("南山区")
    candidates = [
        QfangCandidate(
            name="华侨新村",
            aliases=(),
            detail_url="https://shenzhen.qfang.com/garden/desc/nanshan",
            evidence="华侨新村 南山 南头 2004年建 住宅",
            years=(2004,),
        ),
        QfangCandidate(
            name="华侨新村",
            aliases=(),
            detail_url="https://shenzhen.qfang.com/garden/desc/baoan",
            evidence="华侨新村 宝安 新安 2008年建 住宅",
            years=(2008,),
        ),
    ]

    selected = select_qfang_candidate(candidates, record)

    assert selected is not None
    assert selected.detail_url.endswith("nanshan")
    assert "/garden/nanshan?" in build_qfang_search_url(record, record.name)


def test_load_result_statuses_uses_the_last_result_for_each_community(tmp_path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(
        "{\"community_id\": 1, \"status\": \"PENDING\"}\n"
        "{\"community_id\": 2, \"status\": \"MANUAL_REVIEW\"}\n"
        "{\"community_id\": 1, \"status\": \"SUCCESS\"}\n",
        encoding="utf-8",
    )

    assert load_result_statuses(results) == {1: "SUCCESS", 2: "MANUAL_REVIEW"}


def test_load_qfang_success_ids_only_selects_latest_qfang_success(tmp_path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(
        "{\"community_id\": 1, \"status\": \"SUCCESS\", \"rule\": \"QFANG_LATEST_WITHIN_1_YEAR\"}\n"
        "{\"community_id\": 2, \"status\": \"SUCCESS\", \"rule\": \"SINGLE_OR_CONSISTENT\"}\n"
        "{\"community_id\": 3, \"status\": \"SUCCESS\", \"rule\": \"QFANG_LATEST_WITHIN_1_YEAR\"}\n"
        "{\"community_id\": 3, \"status\": \"MANUAL_REVIEW\", \"rule\": \"QFANG_REPORTED_YEAR_RANGE_OVER_1\"}\n",
        encoding="utf-8",
    )

    assert load_qfang_success_ids(results) == {1}


def test_aggregate_fang_uses_latest_year_when_sources_differ_by_one() -> None:
    results = aggregate_fang_results(
        [
            {
                "community_id": 1,
                "status": "SUCCESS",
                "build_year": 2004,
                "detail_name": "花好园",
                "detail_url": "https://www.fang.com/xiaoqu/sz-a/",
            },
            {
                "community_id": 1,
                "status": "SUCCESS",
                "build_year": 2005,
                "detail_name": "花好园",
                "detail_url": "https://www.fang.com/xiaoqu/sz-b/",
            },
        ],
        manual_ids=set(),
    )

    assert results[0]["status"] == "SUCCESS"
    assert results[0]["build_year"] == 2005
    assert results[0]["rule"] == "FANG_LATEST_WITHIN_1_YEAR"


def test_aggregate_fang_keeps_wide_year_gap_for_manual_review() -> None:
    results = aggregate_fang_results(
        [
            {"community_id": 1, "status": "SUCCESS", "build_year": 2000},
            {"community_id": 1, "status": "SUCCESS", "build_year": 2003},
        ],
        manual_ids=set(),
    )

    assert results[0]["status"] == "MANUAL_REVIEW"
    assert results[0]["build_year"] is None
