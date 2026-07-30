# -*- coding: utf-8 -*-
"""行舟深房 adapter 的离线契约测试。"""

import asyncio
import time
from datetime import date, timedelta

import pytest

from app.platforms.adapters import xzsfbj as adapter_module
from app.platforms.adapters.xzsfbj import (
    ApiResponseError,
    Blocked,
    DealPageRisk,
    LoginExpired,
    XzsfbjApiAdapter,
    _check_api_error,
)
from app.core.models import InquiryRequest, ListingSnapshot, PlatformResult
from app.core.status import PlatformHealthStatus, PlatformResultStatus
from app.platforms import xzsfbj_constants as constants
from app.platforms.xzsfbj import XzsfbjPlatformAdapter


def test_registry_adapter_uses_interface_session_and_city_boundary():
    adapter = XzsfbjPlatformAdapter()
    assert adapter.code == "xzsfbj"
    assert adapter.start_url == "about:blank"
    assert adapter.uses_browser is False
    assert adapter.detect_block("about:blank", "") == (False, "")
    assert "无需网页登录" in adapter.ready_confirmation_hint


def test_interface_session_does_not_create_browser_page():
    adapter = XzsfbjPlatformAdapter()

    session = asyncio.run(adapter.open_session())

    assert session.page is None


def test_collect_closes_external_session_after_collection(monkeypatch):
    adapter = XzsfbjPlatformAdapter()
    closed = []
    result = PlatformResult(
        name="行舟深房",
        status=PlatformResultStatus.NO_DATA,
        reason="测试结果",
    )

    async def fake_collect(_request):
        return result

    monkeypatch.setattr(adapter._api, "collect", fake_collect)
    monkeypatch.setattr(adapter, "close_external_session", lambda: closed.append(True))

    actual = asyncio.run(
        adapter.collect(
            None,
            None,
            InquiryRequest(community_name="测试小区", area=89.5, city="深圳"),
        )
    )

    assert actual is result
    assert closed == [True]


def test_check_ready_guides_first_token_capture_without_browser_login(monkeypatch):
    adapter = XzsfbjPlatformAdapter()
    monkeypatch.setattr(adapter._api, "dependencies_ready", lambda: (True, "READY"))

    ready, message = asyncio.run(adapter.check_ready(object()))

    assert ready is True
    assert "无需网页登录" in message
    assert "自动捕获 token" in message


def test_api_error_mapping_preserves_platform_specific_risk():
    try:
        _check_api_error("deals", {"errCode": "40001", "errMsg": "登录失效"})
    except LoginExpired:
        pass
    else:
        raise AssertionError("40001 should map to LoginExpired")

    try:
        _check_api_error("deals", {"errCode": "500", "errMsg": "访问过于频繁，限制访问"})
    except Blocked:
        pass
    else:
        raise AssertionError("risk message should map to Blocked")

    try:
        _check_api_error("deals", {"errCode": "500", "errMsg": "未知错误"})
    except ApiResponseError:
        pass
    else:
        raise AssertionError("unknown error should map to ApiResponseError")

    try:
        _check_api_error("deals_page_2", {"errCode": "40002", "errMsg": ""})
    except DealPageRisk:
        pass
    else:
        raise AssertionError("40002 on a deal page should map to DealPageRisk")


def test_dependency_check_does_not_start_bridge(monkeypatch, tmp_path):
    adapter = XzsfbjApiAdapter()
    monkeypatch.setattr(
        constants,
        "resolve_xq_data_file",
        lambda: tmp_path / "missing.json",
    )
    ready, reason = adapter.dependencies_ready()

    assert ready is False
    assert "小区索引不存在" in reason


def test_dependency_check_reports_missing_pycryptodome(monkeypatch, tmp_path):
    adapter = XzsfbjApiAdapter()
    xq_data = tmp_path / "xqData.json"
    xq_data.write_text("{}", encoding="utf-8")
    bridge_dir = tmp_path / "bridge"
    (bridge_dir / "src").mkdir(parents=True)
    (bridge_dir / "node_modules" / "frida").mkdir(parents=True)
    (bridge_dir / "src" / "index.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(constants, "resolve_xq_data_file", lambda: xq_data)
    monkeypatch.setattr(constants, "resolve_wmpf_bridge_dir", lambda: bridge_dir)
    monkeypatch.setattr(adapter_module.shutil, "which", lambda name: "node")
    monkeypatch.setattr(
        adapter_module.importlib.util,
        "find_spec",
        lambda name: None if name == "Crypto.Cipher" else object(),
    )

    ready, reason = adapter.dependencies_ready()

    assert ready is False
    assert "pycryptodome" in reason


def test_xzsfbj_aes_key_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv(constants.AES_KEY_ENV, "1234567890abcdef")
    assert constants.get_aes_key() == b"1234567890abcdef"

    monkeypatch.delenv(constants.AES_KEY_ENV)
    with pytest.raises(RuntimeError, match="XZSFBJ_AES_KEY"):
        constants.get_aes_key()


def test_xzsfbj_aes_key_rejects_invalid_length(monkeypatch):
    monkeypatch.setenv(constants.AES_KEY_ENV, "too-short")
    with pytest.raises(RuntimeError, match="16、24 或 32"):
        constants.get_aes_key()


def test_collect_one_returns_service_compatible_deal_dicts(monkeypatch):
    adapter = XzsfbjApiAdapter()

    async def fake_deals(*args, **kwargs):
        return 1, [{
            "acreage": 92,
            "unitPrice": 3.3,
            "price": 304,
            "date": (date.today() - timedelta(days=10)).isoformat(),
        }]

    async def fake_sales(*args, **kwargs):
        return [{"id": "h1", "acreage": 91.5, "unitPrice": 4.8, "price": 450}]

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter, "_fetch_deals", fake_deals)
    monkeypatch.setattr(adapter, "_fetch_sales", fake_sales)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)

    result = asyncio.run(
        adapter.collect_one(
            object(), {"name": "月亮湾花园", "regionId": 2215},
            91.5, "token", "test", time.time(),
        )
    )

    assert result.status == "SUCCESS"
    assert result.deal_records == [{
        "area": 92.0,
        "date": (date.today() - timedelta(days=10)).isoformat(),
        "total_price": 304.0,
        "price": 33000.0,
    }]


def test_collect_one_keeps_partial_deals_after_deal_page_risk(monkeypatch):
    adapter = XzsfbjApiAdapter()
    deal_calls = 0
    sleep_calls = []

    async def fake_deals(*args, **kwargs):
        nonlocal deal_calls
        deal_calls += 1
        if deal_calls == 1:
            page_one_record = {
                "acreage": 92,
                "unitPrice": 3.3,
                "price": 304,
                "date": (date.today() - timedelta(days=10)).isoformat(),
            }
            return 60, [page_one_record.copy() for _ in range(constants.DEAL_PAGE_SIZE)]
        raise DealPageRisk("deals_page_2 平台风控 errCode=40002 msg=")

    async def fake_sales(*args, **kwargs):
        return [{"id": "h1", "acreage": 91.5, "unitPrice": 4.8, "price": 450}]

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(adapter, "_fetch_deals", fake_deals)
    monkeypatch.setattr(adapter, "_fetch_sales", fake_sales)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(adapter_module.random, "uniform", lambda _min, _max: 0)

    result = asyncio.run(
        adapter.collect_one(
            object(), {"name": "月亮湾花园", "regionId": 2215},
            91.5, "token", "test", time.time(),
        )
    )

    assert result.status == PlatformResultStatus.SUCCESS
    assert result.deal_records[0]["price"] == 33000.0
    assert len(result.quote_prices) == 1
    assert "平台风控" in result.reason
    assert "保留此前成功获取的30条" in result.reason
    assert deal_calls == 5
    assert sleep_calls.count(constants.DEAL_PAGE_RISK_RETRY_DELAY) == 3


def test_merge_candidate_results_keeps_one_platform_result_and_all_phase_records():
    first = PlatformResult(
        name="行舟深房",
        status=PlatformResultStatus.SUCCESS,
        quote_prices=[48000.0],
        deal_prices=[33000.0],
        deal_records=[{"area": 92.0, "price": 33000.0}],
        listing_snapshots=[
            ListingSnapshot(
                house_id="h1",
                community_name="前海花园一期",
                area=91.5,
                unit_price=48000.0,
            )
        ],
    )
    second = PlatformResult(
        name="行舟深房",
        status=PlatformResultStatus.SUCCESS,
        quote_prices=[51000.0],
        deal_prices=[35000.0],
        deal_records=[{"area": 92.0, "price": 35000.0}],
        listing_snapshots=[
            ListingSnapshot(
                house_id="h2",
                community_name="前海花园二期",
                area=91.8,
                unit_price=51000.0,
            )
        ],
    )

    merged = XzsfbjApiAdapter._merge_candidate_results(
        [first, second], "request-1", time.time()
    )

    assert merged.status == PlatformResultStatus.SUCCESS
    assert merged.quote_prices == [48000.0, 51000.0]
    assert merged.deal_prices == [33000.0, 35000.0]
    assert len(merged.deal_records) == 2
    assert {item.house_id for item in merged.listing_snapshots} == {"h1", "h2"}
    assert "2" in (merged.reason or "")


def test_missing_initial_token_enters_manual_recovery_and_resumes(monkeypatch):
    adapter = XzsfbjApiAdapter()
    monkeypatch.setattr(
        adapter,
        "_load_communities",
        lambda: [{"name": "Target", "area": "District", "regionId": 1}],
    )
    tokens = iter([None, "recovered-token"])
    token_calls = []
    states = []

    async def fake_token(*, force=False, reason="", request_id=None):
        token_calls.append((force, request_id))
        return next(tokens)

    async def fake_wait(context, *, attempt=1):
        assert context == "行舟深房(xzsfbj)/接口"
        assert attempt == 1

    async def fake_notify(context, state, reason):
        states.append(state)

    async def no_wait():
        return None

    async def fake_collect_one(client, community, area, token, request_id, started_at):
        assert token == "recovered-token"
        return PlatformResult(
            name="行舟深房",
            status=PlatformResultStatus.SUCCESS,
            quote_prices=[40000.0],
            deal_prices=[30000.0],
        )

    monkeypatch.setattr(adapter, "_get_token", fake_token)
    monkeypatch.setattr(adapter, "_wait_between_communities", no_wait)
    monkeypatch.setattr(adapter, "collect_one", fake_collect_one)
    monkeypatch.setattr(adapter_module, "wait_for_manual_unblock", fake_wait)
    monkeypatch.setattr(adapter_module, "notify_manual_verify_state", fake_notify)

    result = asyncio.run(
        adapter.collect(
            InquiryRequest(
                community_name="Target",
                administrative_district="District",
                area=91.5,
                request_id="request-initial-token",
            )
        )
    )

    assert result.status == PlatformResultStatus.SUCCESS
    assert token_calls == [
        (False, "request-initial-token"),
        (True, "request-initial-token"),
    ]
    assert states == [
        PlatformHealthStatus.WAIT_MANUAL_VERIFY,
        PlatformHealthStatus.READY,
    ]


def test_repeated_token_capture_failure_keeps_waiting_for_manual_recovery(monkeypatch):
    adapter = XzsfbjApiAdapter()
    tokens = iter([None, None, "recovered-token"])
    attempts = []
    states = []

    async def fake_token(*, force=False, reason="", request_id=None):
        return next(tokens)

    async def fake_wait(context, *, attempt=1):
        attempts.append(attempt)

    async def fake_notify(context, state, reason):
        states.append(state)

    monkeypatch.setattr(adapter, "_get_token", fake_token)
    monkeypatch.setattr(adapter_module, "wait_for_manual_unblock", fake_wait)
    monkeypatch.setattr(adapter_module, "notify_manual_verify_state", fake_notify)

    token = asyncio.run(
        adapter._recover_token_after_manual(
            "token capture timeout", "request-repeated-token"
        )
    )

    assert token == "recovered-token"
    assert attempts == [1, 2, 3]
    assert states == [
        PlatformHealthStatus.WAIT_MANUAL_VERIFY,
        PlatformHealthStatus.WAIT_MANUAL_VERIFY,
        PlatformHealthStatus.WAIT_MANUAL_VERIFY,
        PlatformHealthStatus.READY,
    ]


def test_manual_recovery_failure_returns_without_entering_aggregation(monkeypatch):
    adapter = XzsfbjApiAdapter()

    async def fake_wait(context, *, attempt=1):
        raise EOFError("no interactive input")

    monkeypatch.setattr(adapter_module, "wait_for_manual_unblock", fake_wait)

    token = asyncio.run(
        adapter._recover_token_after_manual("token capture timeout", "request-eof")
    )

    assert token is None


def test_collect_merges_all_residential_phases_and_skips_no_data(monkeypatch):
    adapter = XzsfbjApiAdapter()
    monkeypatch.setattr(
        adapter,
        "_load_communities",
        lambda: [
            {"name": "前海花园一期", "area": "南山区", "regionId": 1},
            {"name": "前海花园二期", "area": "南山区", "regionId": 2},
            {"name": "前海花园三期", "area": "南山区", "regionId": 3},
        ],
    )

    async def fake_token(*, reason="", request_id=None):
        return "token"

    async def no_wait():
        return None

    async def fake_collect_one(client, community, area, token, request_id, started_at):
        if community["regionId"] == 2:
            return PlatformResult(
                name="行舟深房",
                status=PlatformResultStatus.NO_DATA,
                reason="本期无在售",
            )
        return PlatformResult(
            name="行舟深房",
            status=PlatformResultStatus.SUCCESS,
            quote_prices=[40000.0 + community["regionId"] * 1000],
            deal_prices=[30000.0 + community["regionId"] * 1000],
            listing_snapshots=[
                ListingSnapshot(
                    house_id=f"h{community['regionId']}",
                    community_name=community["name"],
                    area=91.5,
                    unit_price=40000.0 + community["regionId"] * 1000,
                )
            ],
        )

    monkeypatch.setattr(adapter, "_get_token", fake_token)
    monkeypatch.setattr(adapter, "_wait_between_communities", no_wait)
    monkeypatch.setattr(adapter, "collect_one", fake_collect_one)

    result = asyncio.run(
        adapter.collect(
            InquiryRequest(
                community_name="前海花园",
                administrative_district="南山区",
                area=91.5,
                request_id="request-1",
            )
        )
    )

    assert result.status == PlatformResultStatus.SUCCESS
    assert result.quote_prices == [41000.0, 43000.0]
    assert result.deal_prices == [31000.0, 33000.0]
    assert len(result.listing_snapshots) == 2
