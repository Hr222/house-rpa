# -*- coding: utf-8 -*-
"""询价任务弱持久化。

快照只属于询价编排层：它保存已确认的小区身份和 RPA 采集请求，
用于进程异常后的重入队。RPA 运行时不读取、写入或删除这些文件。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.inquiry.models import ConfirmedCommunityContext
from app.rpa.core.models import InquiryRequest


log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
INQUIRY_PERSIST_DIR = BASE_DIR / "persist" / "inquiries"


@dataclass(frozen=True, slots=True)
class InquiryTaskSnapshot:
    """可恢复询价任务的完整编排上下文。"""

    task_id: str
    community_id: int
    created_at: float
    request: InquiryRequest
    confirmed_community: ConfirmedCommunityContext

    def to_dict(self) -> dict:
        """转换为稳定的磁盘快照结构。"""
        return {
            "task_id": self.task_id,
            "community_id": self.community_id,
            "created_at": self.created_at,
            "request": asdict(self.request),
            "confirmed_community": asdict(self.confirmed_community),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InquiryTaskSnapshot":
        """从磁盘快照重建不可变的小区上下文和采集请求。"""
        task_id = str(data["task_id"])
        request_data = data["request"]
        context_data = data["confirmed_community"]
        community_id = int(data["community_id"])
        if request_data.get("request_id") != task_id:
            raise ValueError("request_id 与 task_id 不一致")
        if context_data.get("request_id") != task_id:
            raise ValueError("confirmed_community.request_id 与 task_id 不一致")
        if int(context_data["community_id"]) != community_id:
            raise ValueError("confirmed_community.community_id 与快照不一致")
        return cls(
            task_id=task_id,
            community_id=community_id,
            created_at=float(data["created_at"]),
            request=InquiryRequest(
                community_name=request_data["community_name"],
                area=float(request_data["area"]),
                city=request_data.get("city", "深圳"),
                administrative_district=request_data.get("administrative_district"),
                request_id=request_data.get("request_id"),
                platform_listing_pages=request_data.get("platform_listing_pages") or {},
                platform_deal_pages=request_data.get("platform_deal_pages") or {},
            ),
            confirmed_community=ConfirmedCommunityContext(
                community_id=int(context_data["community_id"]),
                community_group_id=int(context_data["community_group_id"]),
                city=context_data["city"],
                administrative_district=context_data["administrative_district"],
                canonical_name=context_data["canonical_name"],
                aliases=tuple(context_data.get("aliases", ())),
                phase=context_data.get("phase"),
                area=float(context_data["area"]),
                request_id=context_data.get("request_id"),
            ),
        )


def _persist_dir() -> Path:
    INQUIRY_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    return INQUIRY_PERSIST_DIR


def _task_file(task_id: str) -> Path:
    return _persist_dir() / f"{task_id}.json"


def save_pending_task(snapshot: InquiryTaskSnapshot) -> None:
    """原子写入一个可恢复的询价任务快照。"""
    task_file = _task_file(snapshot.task_id)
    temporary_file = task_file.with_suffix(".json.tmp")
    temporary_file.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_file, task_file)
    log.info(
        "询价任务快照已写入: task_id=%s community_id=%s",
        snapshot.task_id,
        snapshot.community_id,
    )


def delete_pending_task(task_id: str) -> None:
    """删除已正常结束的询价任务快照。"""
    task_file = _task_file(task_id)
    try:
        task_file.unlink()
        log.info("询价任务快照已删除: task_id=%s", task_id)
    except FileNotFoundError:
        pass


def clear_pending_tasks() -> int:
    """清空全部残留询价任务快照（测试模式启动时使用），返回清除数量。"""
    cleared = 0
    for task_file in _persist_dir().glob("*.json"):
        try:
            task_file.unlink()
            cleared += 1
        except OSError:
            log.warning("残留询价任务快照删除失败: %s", task_file.name, exc_info=True)
    if cleared:
        log.info("已清空 %d 个残留询价任务快照", cleared)
    return cleared


def load_pending_tasks() -> list[InquiryTaskSnapshot]:
    """加载本编排层目录中可解析的残留任务，按创建时间恢复。"""
    snapshots: list[InquiryTaskSnapshot] = []
    for task_file in _persist_dir().glob("*.json"):
        try:
            data = json.loads(task_file.read_text(encoding="utf-8"))
            snapshot = InquiryTaskSnapshot.from_dict(data)
            if snapshot.task_id != task_file.stem:
                raise ValueError("task_id 与文件名不一致")
            snapshots.append(snapshot)
        except Exception:
            log.warning("询价任务快照无法恢复，已跳过: %s", task_file.name, exc_info=True)
    snapshots.sort(key=lambda item: (item.created_at, item.task_id))
    if snapshots:
        log.info("发现 %d 个待恢复询价任务", len(snapshots))
    return snapshots
