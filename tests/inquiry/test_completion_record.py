# -*- coding: utf-8 -*-
"""询价完成后编排落库：公共入口被调用、失败不阻断返回。"""
import asyncio

import pytest

from app.inquiry.completion import InquiryCompletionOrchestrator
from app.inquiry.models import ConfirmedCommunityContext
from app.property_records.ingestion import IngestionReport
from app.rpa.core.models import InquiryRequest, PlatformResult, RPACollectionResult
from app.rpa.core.status import PlatformResultStatus


def _context() -> ConfirmedCommunityContext:
    return ConfirmedCommunityContext(
        community_id=5380,
        community_group_id=999,
        city="深圳",
        administrative_district="宝安区",
        canonical_name="佳兆业樾伴山",
        aliases=(),
        phase=None,
        area=90.0,
        request_id="completion-record-test",
    )


def _request() -> InquiryRequest:
    return InquiryRequest(
        community_name="佳兆业樾伴山",
        area=90.0,
        city="深圳",
        request_id="completion-record-test",
        platform_listing_pages={"ke": "https://sz.ke.com/ershoufang/c1"},
    )


def _collection() -> RPACollectionResult:
    result = PlatformResult(
        name="贝壳",
        status=PlatformResultStatus.SUCCESS,
        listing_page_url="https://sz.ke.com/ershoufang/c1",
        deal_page_url=None,
    )
    return RPACollectionResult(platform_results=[result])


def test_complete_records_success_platform_before_evaluate(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_record(**kwargs):
        calls.append(kwargs)
        return IngestionReport()

    monkeypatch.setattr("app.inquiry.completion.record_platform_result", fake_record)
    handler = InquiryCompletionOrchestrator().handler_for(_context())
    payload = asyncio.run(handler(_request(), _collection()))
    assert calls, "落库公共入口应被调用"
    assert calls[0]["community_id"] == 5380
    assert calls[0]["result"].listing_page_url.startswith("https")
    assert payload is not None


def test_complete_record_failure_does_not_block_return(monkeypatch) -> None:
    def boom(**kwargs):
        raise RuntimeError("落库失败（如小区身份校验不通过）")

    monkeypatch.setattr("app.inquiry.completion.record_platform_result", boom)
    handler = InquiryCompletionOrchestrator().handler_for(_context())
    # 落库抛异常仅 warning，complete 仍返回完整 payload（估价不受阻）
    payload = asyncio.run(handler(_request(), _collection()))
    assert payload is not None
    assert payload.task_result["success"] is False  # 无在售快照 → NO_DATA 正常返回


def test_complete_without_context_skips_record(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.inquiry.completion.record_platform_result", fake_record)
    handler = InquiryCompletionOrchestrator().handler_for(None)
    payload = asyncio.run(handler(_request(), _collection()))
    assert not calls
    assert payload is not None
