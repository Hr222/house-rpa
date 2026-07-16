# -*- coding: utf-8 -*-
"""崩溃恢复（任务弱持久化重启续跑）单元测试。

验证：服务首次全部就绪时，从 persist/ 恢复崩溃前未完成任务，且只恢复一次。
不启动浏览器，只测 _refresh_service_status 触发 _restore_pending_tasks 的逻辑。
"""

import json
from pathlib import Path
from unittest import mock

from app.runtime import RPARuntime, PlatformRuntimeState


def _make_runtime_no_browsers():
    """构造一个不启动浏览器的 RPARuntime。

    __init__ 只做内存赋值，不连浏览器；browsers 为空 dict，
    _tile_after_login 因 hasattr/空列表保护而安全返回。
    """
    rt = RPARuntime()
    # 手动造两个平台状态（绕过 start 的浏览器流程）
    rt.platform_states = {
        "ke": PlatformRuntimeState(
            code="ke", name="贝壳", start_url="x", status="WAIT_LOGIN", message="m",
        ),
        "ajk": PlatformRuntimeState(
            code="ajk", name="安居客", start_url="y", status="WAIT_LOGIN", message="m",
        ),
    }
    return rt


def _write_task_file(persist_dir, task_id, community="绿景虹湾", amin=70, amax=90):
    """写一个残留任务 JSON（模拟崩溃前入队但未完成）。"""
    (persist_dir / f"{task_id}.json").write_text(
        json.dumps(
            {"community_name": community, "area_min": amin, "area_max": amax, "city": "深圳"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_restore_runs_once_when_all_ready(tmp_path):
    """全部平台就绪时，恢复一次残留任务到队列。"""
    persist = tmp_path / "persist"
    persist.mkdir()
    _write_task_file(persist, "crash-001")
    _write_task_file(persist, "crash-002")

    with mock.patch("app.utils.task_store.config.PERSIST_DIR", persist), \
         mock.patch("app.runtime.config.PERSIST_DIR", persist):
        rt = _make_runtime_no_browsers()
        assert rt._restored is False

        # 全部置 READY → 触发恢复
        for s in rt.platform_states.values():
            s.status = "READY"
        rt._refresh_service_status()

        assert rt._restored is True
        assert rt.status == "READY"
        # 两个残留任务都进了队列
        assert rt.queue.qsize() == 2
        queued = set()
        while not rt.queue.empty():
            queued.add(rt.queue.get_nowait())
        assert queued == {"crash-001", "crash-002"}
        # 也进了 tasks 记录
        assert set(rt.tasks.keys()) == {"crash-001", "crash-002"}


def test_restore_runs_only_once(tmp_path):
    """再次调用 _refresh_service_status 不会重复恢复（_restored 标志保护）。"""
    persist = tmp_path / "persist"
    persist.mkdir()
    _write_task_file(persist, "crash-001")

    with mock.patch("app.utils.task_store.config.PERSIST_DIR", persist), \
         mock.patch("app.runtime.config.PERSIST_DIR", persist):
        rt = _make_runtime_no_browsers()
        for s in rt.platform_states.values():
            s.status = "READY"

        rt._refresh_service_status()
        assert rt.queue.qsize() == 1

        # 模拟状态抖动：先降级，再全部就绪，不应再次恢复
        rt.platform_states["ke"].status = "WAIT_LOGIN"
        rt._refresh_service_status()
        rt.platform_states["ke"].status = "READY"
        rt._refresh_service_status()

        assert rt.queue.qsize() == 1  # 仍是 1，没有重复入队


def test_no_restore_until_all_ready(tmp_path):
    """只有部分平台就绪时，不触发恢复。"""
    persist = tmp_path / "persist"
    persist.mkdir()
    _write_task_file(persist, "crash-001")

    with mock.patch("app.utils.task_store.config.PERSIST_DIR", persist), \
         mock.patch("app.runtime.config.PERSIST_DIR", persist):
        rt = _make_runtime_no_browsers()
        # 只让 ke 就绪，ajk 仍 WAIT_LOGIN
        rt.platform_states["ke"].status = "READY"
        rt._refresh_service_status()

        assert rt._restored is False
        assert rt.queue.qsize() == 0  # 未全部就绪，不恢复
