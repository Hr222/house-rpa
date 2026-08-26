# -*- coding: utf-8 -*-
"""腾讯地图地理编码请求构造测试。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from app.community_data.geocoder import TencentGeocoder


def test_signed_url_encodes_request_but_signs_raw_parameters() -> None:
    geocoder = TencentGeocoder(key="test-key", secret_key="test-secret")
    url = geocoder.build_url("深圳市罗湖区城市天地广场")
    query = parse_qs(urlsplit(url).query)

    assert query["key"] == ["test-key"]
    assert query["address"] == ["深圳市罗湖区城市天地广场"]
    assert len(query["sig"][0]) == 32


def test_unsigned_url_only_contains_key_and_address() -> None:
    geocoder = TencentGeocoder(key="test-key", secret_key="", load_env=False)
    query = parse_qs(urlsplit(geocoder.build_url("深圳市南山区科技园")).query)

    assert set(query) == {"address", "key"}
