# -*- coding: utf-8 -*-
"""询价任务快照路径安全测试。"""

from __future__ import annotations

import pytest

from app.inquiry.task_store import _task_file


@pytest.mark.parametrize("task_id", ["../runtime", r"..\runtime", "nested/task"])
def test_task_file_rejects_path_traversal(monkeypatch, tmp_path, task_id) -> None:
    monkeypatch.setattr("app.inquiry.task_store.INQUIRY_PERSIST_DIR", tmp_path)

    with pytest.raises(ValueError, match="路径穿越|持久化目录"):
        _task_file(task_id)
