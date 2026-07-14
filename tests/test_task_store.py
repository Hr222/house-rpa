# -*- coding: utf-8 -*-
"""task_store 持久化模块单元测试。"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from app.utils.task_store import save_task, delete_task, load_pending_tasks


@pytest.fixture
def persist_dir():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch("app.utils.task_store.config.PERSIST_DIR", Path(tmp)):
            yield Path(tmp)


# ---- save + load -----------------------------------------------------------

def test_save_and_load_single_task(persist_dir):
    save_task("task-1", {"community_name": "绿景虹湾", "area_min": 70, "area_max": 90})
    tasks = load_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "task-1"
    assert tasks[0]["community_name"] == "绿景虹湾"
    assert tasks[0]["area_min"] == 70
    assert tasks[0]["area_max"] == 90


def test_save_and_load_multiple_tasks(persist_dir):
    save_task("t1", {"a": 1})
    save_task("t2", {"a": 2})
    save_task("t3", {"a": 3})
    tasks = load_pending_tasks()
    assert len(tasks) == 3
    assert [t["task_id"] for t in tasks] == ["t1", "t2", "t3"]


def test_load_empty_dir_returns_empty_list(persist_dir):
    assert load_pending_tasks() == []


# ---- delete ----------------------------------------------------------------

def test_delete_removes_file(persist_dir):
    save_task("task-x", {"community_name": "测试"})
    delete_task("task-x")
    tasks = load_pending_tasks()
    assert tasks == []


def test_delete_nonexistent_task_no_error(persist_dir):
    # 不应抛异常
    delete_task("nonexistent")


def test_delete_then_re_save(persist_dir):
    save_task("t", {"x": 1})
    delete_task("t")
    save_task("t", {"x": 2})
    tasks = load_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["x"] == 2


# ---- corrupted file --------------------------------------------------------

def test_load_skips_corrupted_json(persist_dir):
    (persist_dir / "bad.json").write_text("not valid json", encoding="utf-8")
    save_task("good", {"ok": True})
    tasks = load_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "good"


# ---- data integrity --------------------------------------------------------

def test_saved_json_is_valid_utf8(persist_dir):
    save_task("zh", {"community_name": "春华四季园", "city": "深圳"})
    raw = (persist_dir / "zh.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["community_name"] == "春华四季园"
    assert data["city"] == "深圳"
