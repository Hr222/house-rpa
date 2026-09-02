# -*- coding: utf-8 -*-
"""询价编排测试的共享构造器。"""

from __future__ import annotations

from app.community_data.models import CommunityRecord


def community(
    *,
    community_id: int = 1,
    community_group_id: int = 1,
    name: str = "示例花园一期",
    phase: str | None = "一期",
    aliases: tuple[str, ...] = ("示例花园1期",),
    estate_type: str = "住宅",
) -> CommunityRecord:
    """构造最小可用小区主数据记录。"""
    return CommunityRecord(
        community_id=community_id,
        community_group_id=community_group_id,
        city="深圳",
        administrative_district="南山区",
        district=None,
        name=name,
        phase=phase,
        build_year=None,
        aliases=aliases,
        address=f"深圳市南山区{name}",
        longitude=None,
        latitude=None,
        coordinate_system=None,
        geocode_status="PENDING",
        geocode_level=None,
        geocode_reliability=None,
        remark=None,
        estate_type=estate_type,
        created_at="2026-08-26T00:00:00+00:00",
        updated_at="2026-08-26T00:00:00+00:00",
    )


class FakeRuntime:
    """只记录入队参数的运行时替身。"""

    def __init__(self) -> None:
        self.calls = []

    async def enqueue_inquiry(
        self,
        request,
        *,
        completion_handler=None,
        terminal_handler=None,
    ):
        self.calls.append((request, completion_handler, terminal_handler))
        return {
            "taskId": "task-001",
            "status": "排队中",
            "statusCode": "QUEUED",
        }


class FakeTaskManager:
    """只记录已确认小区上下文的编排任务管理替身。"""

    recovery_complete = True

    def __init__(self) -> None:
        self.calls = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def submit(self, context):
        self.calls.append(context)
        return {
            "taskId": context.request_id or "task-001",
            "status": "排队中",
            "statusCode": "QUEUED",
        }
