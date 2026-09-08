# -*- coding: utf-8 -*-
"""房源入库的关键安全测试。"""

from __future__ import annotations

import sqlite3
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


def test_listing_updates_by_url_and_can_restore_after_logical_deletion(tmp_path):
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
    assert database.list_listings(community.community_id) == [restored]


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


def test_listing_batch_never_deletes_when_the_effective_list_is_empty(tmp_path):
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

    assert len(database.list_listings(community.community_id)) == 2


def test_listing_batch_does_not_delete_rows_newer_than_observation(tmp_path):
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
    listing = database.upsert_listing(
        listing_url="https://example.com/house/new",
        seen_at="2026-09-08T10:00:00+00:00",
        **common,
    )

    # 较早批次晚到时，不能把较新采集到的挂牌误标为下架。
    deleted = database.mark_listings_not_seen(
        community.community_id,
        "fang",
        ["https://example.com/house/other"],
        updated_at="2026-09-08T09:00:00+00:00",
    )

    assert deleted == []
    current = database.list_listings(community.community_id, include_deleted=True)
    assert current == [listing]
    assert current[0].is_deleted is False

    # 只有不早于该挂牌最后观察时间的批次，才有资格执行下架。
    deleted = database.mark_listings_not_seen(
        community.community_id,
        "fang",
        ["https://example.com/house/other"],
        updated_at="2026-09-08T11:00:00+00:00",
    )

    assert [row.id for row in deleted] == [listing.id]
    assert deleted[0].is_deleted is True


def test_community_platform_page_combines_listing_and_deal_entries(tmp_path):
    database, community = _databases(tmp_path)

    first = database.upsert_community_platform_page(
        community_id=community.community_id,
        source_platform="lj",
        source_community_name=None,
        listing_page_url="HTTPS://Lj.Example/estate/listing/?area=80#top",
    )
    merged = database.upsert_community_platform_page(
        community_id=community.community_id,
        source_platform="lj",
        source_community_name="城市天地",
        deal_page_url="https://lj.example/estate/deal/?page=1",
    )

    assert first.id == merged.id
    assert merged.source_community_name == "城市天地"
    assert merged.listing_page_url == "https://lj.example/estate/listing?area=80"
    assert merged.deal_page_url == "https://lj.example/estate/deal?page=1"
    assert database.get_community_platform_page(
        community.community_id, "lj"
    ) == merged
    assert database.list_community_platform_pages(community.community_id) == [merged]


def test_legacy_community_page_tables_migrate_without_overwriting_entries(tmp_path):
    community_db = CommunityDatabase(tmp_path / "community.sqlite3")
    community = community_db.insert_or_get(
        CommunitySeed(city="深圳", administrative_district="罗湖区", name="城市天地广场")
    )
    records_path = tmp_path / "property_records.sqlite3"
    with sqlite3.connect(records_path) as connection:
        connection.executescript(
            """
            CREATE TABLE community_platform_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                community_id INTEGER NOT NULL,
                source_platform TEXT NOT NULL,
                source_community_name TEXT,
                listing_page_url TEXT,
                deal_page_url TEXT,
                UNIQUE (community_id, source_platform)
            );
            CREATE TABLE community_listing_pages (
                community_id INTEGER NOT NULL,
                source_platform TEXT NOT NULL,
                listing_page_url TEXT NOT NULL
            );
            CREATE TABLE community_deal_pages (
                community_id INTEGER NOT NULL,
                city TEXT NOT NULL,
                administrative_district TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                source_community_name TEXT NOT NULL,
                deal_page_url TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO community_listing_pages
                (community_id, source_platform, listing_page_url)
            VALUES (?, ?, ?)
            """,
            (community.community_id, "ke", "https://ke.example/estate/listing"),
        )
        connection.execute(
            """
            INSERT INTO community_listing_pages
                (community_id, source_platform, listing_page_url)
            VALUES (?, ?, ?)
            """,
            (community.community_id, "lj", "https://lj.example/estate/listing"),
        )
        connection.execute(
            """
            INSERT INTO community_deal_pages (
                community_id, city, administrative_district, source_platform,
                source_community_name, deal_page_url
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                community.community_id,
                "深圳",
                "罗湖区",
                "lj",
                "城市天地",
                "https://lj.example/estate/deal",
            ),
        )
        connection.execute(
            """
            INSERT INTO community_platform_pages (
                community_id, source_platform, source_community_name,
                listing_page_url, deal_page_url
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                community.community_id,
                "lj",
                "当前页面小区名",
                "https://lj.example/estate/current-listing",
                None,
            ),
        )

    database = PropertyRecordsDatabase(records_path, community_database=community_db)

    listing_only = database.get_community_platform_page(community.community_id, "ke")
    merged = database.get_community_platform_page(community.community_id, "lj")
    assert listing_only is not None
    assert listing_only.source_community_name is None
    assert listing_only.listing_page_url == "https://ke.example/estate/listing"
    assert listing_only.deal_page_url is None
    assert merged is not None
    assert merged.source_community_name == "当前页面小区名"
    assert merged.listing_page_url == "https://lj.example/estate/current-listing"
    assert merged.deal_page_url == "https://lj.example/estate/deal"


def test_rpa_ingestion_stores_real_deals_but_not_compatibility_averages(tmp_path):
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
        ],
    )
    compatibility_result = SimpleNamespace(
        name="安居客",
        deal_prices=[52_000],
        deal_records=[],
    )
    compatibility_report = ingestion.ingest_rpa_result(
        community_id=community.community_id,
        city="深圳",
        administrative_district="罗湖区",
        source_community_name="城市天地",
        result=compatibility_result,
    )
    assert len(report.deals) == 1
    assert report.deals[0].total_price_yuan == 3_700_000.0
    assert report.platform_page is not None
    assert report.platform_page.deal_page_url == "https://lj.example/estate/deal"
    assert compatibility_report.deals == ()
    assert len(database.list_deals(community.community_id)) == 1

    with pytest.raises(ValueError, match="不匹配"):
        ingestion.ingest_platform_result(
            community_id=community.community_id,
            city="深圳",
            administrative_district="福田区",
            source_platform="房天下",
            source_community_name="城市天地",
            deal_records=[{}],
        )
