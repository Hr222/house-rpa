# -*- coding: utf-8 -*-
"""回调推送单元测试。用 httpx.MockTransport 模拟服务端，不连真实服务。

注意：项目未引入 pytest-asyncio，故用同步测试函数包 asyncio.run 调用异步逻辑。
通过 notify_result 的 client_factory 参数注入走 MockTransport 的 client，
不 patch 全局 httpx.AsyncClient（避免 MockTransport 内部递归）。
"""

import asyncio

import httpx

from app.utils.callback import notify_result


def _make_handler_and_calls(status_sequence):
    """造一个 MockTransport handler，按 status_sequence 依次返回状态码。"""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(calls["count"], len(status_sequence) - 1)
        calls["count"] += 1
        return httpx.Response(status_sequence[idx], json={"ok": True})

    return handler, calls


def _client_factory_for(handler):
    """返回一个 client 工厂：每次 new 一个走 MockTransport 的 AsyncClient。"""
    def _factory(**kwargs):
        kwargs.pop("transport", None)  # 统一忽略，用我们自己的 MockTransport
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    return _factory


async def _noop_sleep(*_a, **_k):
    return None


def test_notify_success_on_first_try(monkeypatch):
    """服务端 200 → 首次即成功。"""
    handler, calls = _make_handler_and_calls([200])
    monkeypatch.setattr("app.utils.callback.asyncio.sleep", _noop_sleep)

    ok = asyncio.run(notify_result(
        "http://cb.test/callback", "t1", {"x": 1},
        client_factory=_client_factory_for(handler),
    ))
    assert ok is True
    assert calls["count"] == 1


def test_notify_retries_then_succeeds(monkeypatch):
    """前两次 500、第三次 200 → 重试后成功。"""
    handler, calls = _make_handler_and_calls([500, 500, 200])
    monkeypatch.setattr("app.utils.callback.asyncio.sleep", _noop_sleep)

    ok = asyncio.run(notify_result(
        "http://cb.test/callback", "t2", {"x": 1},
        client_factory=_client_factory_for(handler),
    ))
    assert ok is True
    assert calls["count"] == 3


def test_notify_all_fail_returns_false(monkeypatch):
    """全部失败 → 返回 False 且不抛异常。"""
    handler, calls = _make_handler_and_calls([500, 500, 500])
    monkeypatch.setattr("app.utils.callback.asyncio.sleep", _noop_sleep)

    ok = asyncio.run(notify_result(
        "http://cb.test/callback", "t3", {"x": 1},
        client_factory=_client_factory_for(handler),
    ))
    assert ok is False
    assert calls["count"] == 3


def test_notify_skipped_when_url_empty():
    """callback_url 为空 → 直接跳过，返回 False，不发请求。"""
    handler, calls = _make_handler_and_calls([200])
    assert asyncio.run(notify_result(
        None, "t4", {"x": 1}, client_factory=_client_factory_for(handler),
    )) is False
    assert asyncio.run(notify_result(
        "", "t5", {"x": 1}, client_factory=_client_factory_for(handler),
    )) is False
    assert calls["count"] == 0  # 没发任何请求
