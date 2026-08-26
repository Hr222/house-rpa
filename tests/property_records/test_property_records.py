# -*- coding: utf-8 -*-
"""房源记录模块底层 SQLite 行为测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.community_data.database import CommunityDatabase
from app.community_data.models import CommunitySeed
from app.property_records.database import PropertyRecordsDatabase
from app.property_records.ingestion import PropertyRecordsIngestion


def _databases(tmp_path):
    community_db = CommunityDatabase(tmp_path / "community.sqlite3")
    community = community_db.insert_or_get(
        CommunitySeed(city="深圳", administrative_district="罗湖区", name="城市天地广场")
    )
    records_db = PropertyRecordsDatabase(
        tmp_path / "property_records.sqlite3",
        community_database=community_db,
    )
    return records_db, community


def test_deal_same_source_is_idempotent_and_cross_source_is_kept(tmp_path):
    database, community = _databases(tmp_path)
    values = dict(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_community_name="城市天地广场",
        deal_date="2026.06.21",
        area_sqm="88.6㎡",
        total_price="370万",
        unit_price_yuan="41761元/㎡",
    )

    first = database.upsert_deal(source_platform="lj", **values)
    repeat = database.upsert_deal(source_platform="lj", **values)
    database.upsert_deal(source_platform="fang", **values)

    assert first.id == repeat.id
    assert repeat.total_price_yuan == 3_700_000.0
    assert len(database.list_deals(community.community_id)) == 2
    unique = database.list_deals(community.community_id, deduplicate=True)
    assert len(unique) == 1
    assert unique[0].source_platforms == ("fang", "lj")


def test_deal_rejects_missing_community_or_mismatched_location(tmp_path):
    database, community = _databases(tmp_path)
    values = dict(
        community_id=community.community_id,
        city="深圳",
        administrative_district="福田区",
        source_platform="lj",
        source_community_name="城市天地广场",
        deal_date="2026-06-21",
        area_sqm=88.6,
        total_price=3_700_000,
        unit_price_yuan=41_761,
    )
    with pytest.raises(ValueError, match="不匹配"):
        database.upsert_deal(**values)
    values["community_id"] = 999999
    values["administrative_district"] = "罗湖区"
    with pytest.raises(ValueError, match="不存在"):
        database.upsert_deal(**values)


def test_deal_page_is_one_entry_per_community_and_platform(tmp_path):
    database, community = _databases(tmp_path)
    values = dict(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_community_name="城市天地广场",
        deal_page_url="https://example.com/old",
    )
    first = database.upsert_deal_page(source_platform="fang", **values)
    updated = database.upsert_deal_page(
        source_platform="fang",
        **{**values, "deal_page_url": "https://example.com/new"},
    )
    assert first.id == updated.id
    assert database.list_deal_pages(community.community_id)[0].deal_page_url.endswith("/new")

    with pytest.raises(ValueError, match="完整 HTTP"):
        database.upsert_deal_page(
            community_id=community.community_id,
            city="深圳",
            administrative_district="罗湖区",
            source_platform="fang",
            source_community_name="城市天地广场",
            deal_page_url="not-a-url",
        )


def test_listing_page_is_one_entry_per_community_and_platform(tmp_path):
    database, community = _databases(tmp_path)
    values = dict(
        community_id=community.community_id,
    )
    first = database.upsert_listing_page(
        source_platform="fang",
        listing_page_url="HTTPS://Example.com/community/listing/old/?area=90#top",
        **values,
    )
    updated = database.upsert_listing_page(
        source_platform="fang",
        listing_page_url="https://example.com/community/listing/new",
        **values,
    )

    assert first.id == updated.id
    assert updated.listing_page_url.endswith("/new")
    assert database.list_listing_pages(community.community_id) == [updated]

    with pytest.raises(ValueError, match="完整 HTTP"):
        database.upsert_listing_page(
            community_id=community.community_id,
            source_platform="fang",
            listing_page_url="not-a-url",
        )


def test_listing_updates_by_platform_and_url_and_can_restore(tmp_path):
    database, community = _databases(tmp_path)
    values = dict(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_platform="fang",
        listing_url="HTTPS://Example.com/house/1/?utm_source=share#top",
        source_community_name="城市天地",
        title="首次挂牌",
        layout="3室2厅",
        area_sqm="88.60㎡",
        total_price="500万",
        unit_price_yuan="56433元/㎡",
        seen_at="2026-08-25T10:00:00+00:00",
    )
    first = database.upsert_listing(**values)
    deleted = database.mark_listing_deleted("fang", first.listing_url, "2026-08-25T11:00:00+00:00")
    restored = database.upsert_listing(
        **{**values, "title": "降价挂牌", "total_price": "490万", "seen_at": "2026-08-26T10:00:00+00:00"}
    )

    assert first.id == restored.id
    assert deleted is not None and deleted.is_deleted is True
    assert restored.is_deleted is False
    assert restored.title == "降价挂牌"
    assert restored.total_price_yuan == 4_900_000.0
    assert restored.listing_url == "https://example.com/house/1"
    assert len(database.list_listings(community.community_id)) == 1
    assert len(database.list_listings(community.community_id, include_deleted=True)) == 1

    logs = database.list_listing_logs(first.id)
    assert len(logs) == 2
    assert logs[0].listing_record_id == first.id
    assert logs[0].observed_at == "2026-08-26T10:00:00+00:00"
    assert logs[0].total_price_yuan == 4_900_000.0
    assert logs[0].unit_price_yuan == 56_433.0
    assert logs[1].observed_at == "2026-08-25T10:00:00+00:00"
    assert logs[1].total_price_yuan == 5_000_000.0


def test_older_listing_observation_does_not_overwrite_current_record_or_log(tmp_path):
    database, community = _databases(tmp_path)
    common = dict(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_platform="fang",
        listing_url="https://example.com/house/old-check",
        source_community_name="城市天地广场",
    )
    current = database.upsert_listing(
        **common,
        total_price="500万",
        unit_price_yuan=56_000,
        seen_at="2026-08-26T10:00:00+00:00",
    )
    stale = database.upsert_listing(
        **common,
        total_price="520万",
        unit_price_yuan=58_000,
        seen_at="2026-08-25T10:00:00+00:00",
    )

    assert stale.id == current.id
    assert stale.total_price_yuan == 5_000_000.0
    assert stale.unit_price_yuan == 56_000.0
    assert stale.last_seen_at == "2026-08-26T10:00:00+00:00"
    assert len(database.list_listing_logs(current.id)) == 1


def test_listing_same_url_different_community_is_rejected(tmp_path):
    database, community = _databases(tmp_path)
    other = database.community_database.insert_or_get(
        CommunitySeed(city="深圳", administrative_district="罗湖区", name="另一个小区")
    )
    values = dict(
        city="深圳",
        administrative_district="罗湖区",
        source_platform="lj",
        listing_url="https://example.com/house/2",
        source_community_name="城市天地广场",
        area_sqm=80,
        total_price=4_000_000,
        unit_price_yuan=50_000,
    )
    original = database.upsert_listing(community_id=community.community_id, **values)
    with pytest.raises(ValueError, match="自动改绑"):
        database.upsert_listing(
            community_id=other.community_id,
            **{**values, "source_community_name": "另一个小区"},
        )

    current = database.list_listings(community.community_id, include_deleted=True)
    assert current == [original]
    assert database.list_listings(other.community_id, include_deleted=True) == []
    assert len(database.list_listing_logs(original.id)) == 1


def test_listing_batch_missing_urls_are_logically_deleted_but_empty_batch_is_rejected(tmp_path):
    database, community = _databases(tmp_path)
    common = dict(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_platform="fang",
        source_community_name="城市天地",
        area_sqm=80,
        total_price=4_000_000,
        unit_price_yuan=50_000,
    )
    database.upsert_listing(listing_url="https://example.com/house/1", **common)
    database.upsert_listing(listing_url="https://example.com/house/2", **common)

    with pytest.raises(ValueError, match="observed_urls"):
        database.mark_listings_not_seen(community.community_id, "fang", [])

    deleted = database.mark_listings_not_seen(
        community.community_id,
        "fang",
        ["https://example.com/house/1"],
        updated_at="2026-08-26T10:00:00+00:00",
    )
    assert [record.listing_url for record in deleted] == ["https://example.com/house/2"]
    assert [record.listing_url for record in database.list_listings(community.community_id)] == [
        "https://example.com/house/1"
    ]


def test_ingestion_adapts_rpa_rows_and_reports_incomplete_deals(tmp_path):
    database, community = _databases(tmp_path)
    ingestion = PropertyRecordsIngestion(database)

    report = ingestion.ingest_platform_result(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_platform="链家",
        source_community_name="城市天地",
        deal_page_url="https://lj.example/estate/deal",
        deal_records=[
            {
                "area": "88.6㎡",
                "date": "2026.06.21",
                "total_price": 370,
                "price": "41761元/㎡",
            },
            {"area": "88.6㎡", "date": "2026.06.21", "price": 41761},
        ],
    )

    assert len(report.deals) == 1
    assert report.deals[0].total_price_yuan == 3_700_000.0
    assert len(report.skipped_deals) == 1
    assert report.deal_page is not None
    assert report.deal_page.source_platform == "lj"


def test_ingestion_rpa_result_does_not_store_compatibility_average(tmp_path):
    database, community = _databases(tmp_path)
    ingestion = PropertyRecordsIngestion(database)
    result = SimpleNamespace(
        name="安居客",
        deal_prices=[52_000],
        deal_records=[],
    )

    report = ingestion.ingest_rpa_result(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_community_name="城市天地",
        result=result,
    )

    assert report.deals == ()
    assert database.list_deals(community.community_id) == []


def test_ingestion_updates_listing_by_url_and_validates_before_batch(tmp_path):
    database, community = _databases(tmp_path)
    ingestion = PropertyRecordsIngestion(database)
    common = dict(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_platform="房天下",
        source_community_name="城市天地",
        listing_page_url="https://fang.example/community/listing",
    )

    first = ingestion.ingest_platform_result(
        **common,
        listing_records=[
            {
                "listing_url": "https://fang.example/house/1?utm_source=share",
                "area": "88㎡",
                "total_price_wan": 500,
                "unit_price_yuan": 56818,
            }
        ],
        seen_at="2026-08-25T10:00:00+00:00",
    )
    second = ingestion.ingest_platform_result(
        **common,
        listing_records=[
            {
                "listing_url": "https://fang.example/house/1",
                "title": "降价挂牌",
                "total_price_wan": 490,
            }
        ],
        seen_at="2026-08-26T10:00:00+00:00",
    )

    assert len(first.listings) == 1
    assert first.listing_page is not None
    assert first.listing_page.source_platform == "fang"
    assert len(second.listings) == 1
    assert second.listings[0].id == first.listings[0].id
    assert second.listings[0].total_price_yuan == 4_900_000.0
    assert len(database.list_listings(community.community_id)) == 1

    with pytest.raises(ValueError, match="不匹配"):
        ingestion.ingest_platform_result(
            **{
                **common,
                "administrative_district": "福田区",
            },
            deal_records=[{}],
        )
