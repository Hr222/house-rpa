# -*- coding: utf-8 -*-
"""询价 HTTP 入口的小区准入测试。"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.inquiry.orchestrator import InquiryOrchestrator
from app.api import create_app
from tests.inquiry.helpers import FakeRuntime, FakeTaskManager, community


def test_create_inquiry_handles_community_resolution_before_enqueue() -> None:
    runtime = FakeRuntime()
    task_manager = FakeTaskManager()
    first = community()
    second = replace(first, community_id=2, name="示例花园二期", phase="二期")

    def resolver(_city: str, _district: str, community_name: str, _estate_type=None):
        if community_name == "不存在小区":
            return []
        if community_name == "示例花园":
            return [first, second]
        return [first]

    app = create_app(
        runtime=runtime,
        manage_runtime=False,
        inquiry_orchestrator=InquiryOrchestrator(task_manager, community_resolver=resolver),
    )
    with TestClient(app) as client:
        not_found = client.post(
            "/inquiries",
            json={
                "city": "深圳",
                "administrativeDistrict": "南山区",
                "communityName": "不存在小区",
                "area": 89.5,
            },
        )
        phase_required = client.post(
            "/inquiries",
            json={
                "city": "深圳",
                "administrativeDistrict": "南山区",
                "communityName": "示例花园",
                "area": 89.5,
            },
        )
        accepted = client.post(
            "/inquiries",
            json={
                "city": "深圳",
                "administrativeDistrict": "南山区",
                "communityName": "示例花园一期",
                "area": 89.5,
                "requestId": "order-001",
            },
        )

    assert not_found.status_code == 200
    assert not_found.json()["code"] == "COMMUNITY_NOT_FOUND"
    assert phase_required.status_code == 200
    assert phase_required.json()["code"] == "COMMUNITY_PHASE_REQUIRED"
    assert phase_required.json()["data"]["candidates"][0]["canonicalName"] == "示例花园一期"
    assert accepted.status_code == 202
    assert accepted.json()["code"] == "ACCEPTED"
    assert accepted.json()["data"]["taskId"] == "task-001"
    assert accepted.json()["data"]["requestId"] == "order-001"
    assert len(task_manager.calls) == 1
    assert task_manager.calls[0].community_id == first.community_id
