# -*- coding: utf-8 -*-
"""任务持久化（轻量级 JSON 文件）。

每个任务一个 JSON 文件，写于请求入队后，删于工作完成后。
正常情况下存储目录永远为空，仅在进程崩溃 / 异常退出时残留，
重启后通过 load_pending_tasks 恢复未完成的任务。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from app.core import config

log = logging.getLogger(__name__)


def _persist_dir() -> Path:
    persist_dir = config.PERSIST_DIR
    persist_dir.mkdir(parents=True, exist_ok=True)
    return persist_dir


def _task_file(task_id: str) -> Path:
    return _persist_dir() / f"{task_id}.json"


def save_task(task_id: str, request_data: dict) -> None:
    """收到请求入队后写一条 JSON。"""
    task_file = _task_file(task_id)
    task_file.write_text(
        json.dumps(request_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("task persisted: %s", task_file.name)


def delete_task(task_id: str) -> None:
    """工作完成后删一条 JSON。"""
    task_file = _task_file(task_id)
    try:
        task_file.unlink()
        log.info("task removed: %s", task_file.name)
    except FileNotFoundError:
        pass


def load_pending_tasks() -> list[dict]:
    """启动时读所有残留任务，用于恢复。

    返回 [{task_id, ...request_data}, ...] 列表。
    """
    persist_dir = _persist_dir()
    tasks = []
    for task_file in sorted(persist_dir.glob("*.json")):
        try:
            data = json.loads(task_file.read_text(encoding="utf-8"))
            data["task_id"] = task_file.stem
            tasks.append(data)
        except Exception as exc:
            log.warning("failed to load pending task %s: %s", task_file.name, exc)
    if tasks:
        log.info("found %d pending task(s) to restore", len(tasks))
    return tasks
