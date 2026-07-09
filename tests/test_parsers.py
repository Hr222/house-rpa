# -*- coding: utf-8 -*-
from pathlib import Path
from app.parsers import parse_listings, is_ad, find_detail_link

FIXTURE = Path(__file__).parent / "fixtures" / "ke_listing_sample.html"


def load_fixture():
    return FIXTURE.read_text(encoding="utf-8")


class TestParseListings:
    def test_extracts_unit_price(self):
        listings = parse_listings(load_fixture())
        assert len(listings) > 0
        # 实测样本第一条单价 84547
        assert listings[0]["unit_price"] == 84547.0

    def test_extracts_area(self):
        listings = parse_listings(load_fixture())
        assert listings[0]["area"] == 85.16

    def test_excludes_ads(self):
        """广告 li(list_goodhouse_daoliu) 不应出现。"""
        listings = parse_listings(load_fixture())
        # 样本里有 26 个真实房源 + 1 个广告，应得 26
        assert len(listings) == 26


class TestIsAd:
    def test_daoliu_is_ad(self):
        assert is_ad('list_goodhouse_daoliu VIEWDATA') is True

    def test_clear_is_not_ad(self):
        assert is_ad('clear') is False


class TestFindDetailLink:
    def test_finds_agent_card_link(self):
        # 「查看小区详情」链接在 sellListContent 之外的 agent 卡片区，
        # 用完整渲染 HTML 验证（链接在完整页面的右侧 agent 卡片里）
        full = Path(__file__).parent.parent / "ke_rendered.html"
        if not full.exists():
            import pytest
            pytest.skip("ke_rendered.html 不存在（需先跑探测生成）")
        link = find_detail_link(full.read_text(encoding="utf-8"))
        assert link is not None
        assert "xiaoqu" in link
        assert "2411063588287" in link
