# -*- coding: utf-8 -*-
"""公共风控标识和平台规则合并入口测试。"""

import asyncio
import threading
import time

from app.platforms.base import (
    detect_block_with_common,
    detect_common_block,
    is_manual_verify_reason,
    set_manual_verify_lock,
    wait_for_manual_unblock,
    wait_and_reload_after_block,
)


def test_common_risk_url_marker_is_detected():
    blocked, reason = detect_common_block(
        "https://example.test/verifycode?token=1",
        "<html><body>请稍候</body></html>",
    )

    assert blocked is True
    assert "公共URL标识" in reason


def test_common_risk_html_marker_is_detected_without_business_content():
    blocked, reason = detect_common_block(
        "https://example.test/list",
        "<html><body>访问过于频繁，请完成验证</body></html>",
    )

    assert blocked is True
    assert "公共HTML标识" in reason


def test_common_risk_html_does_not_flag_normal_business_page():
    blocked, _ = detect_common_block(
        "https://example.test/list",
        "<html><body>在售房源 50000 元/㎡，小区均价 48000</body></html>",
    )

    assert blocked is False


def test_platform_specific_rule_has_priority_over_common_fallback():
    def platform_detector(url: str, html: str) -> tuple[bool, str]:
        return True, "平台专属安全标识"

    blocked, reason = detect_block_with_common(
        platform_detector,
        "https://example.test/captcha",
        "<html><body>验证码</body></html>",
    )

    assert blocked is True
    assert reason == "平台专属安全标识"


def test_manual_verify_reason_is_classified_for_health_state():
    assert is_manual_verify_reason("命中验证码拦截(公共URL标识)") is True
    assert is_manual_verify_reason("命中人机验证，等待人工处理") is True
    assert is_manual_verify_reason("未检测到已登录标识") is False


def test_manual_verify_prompts_are_serialized(monkeypatch):
    active = 0
    max_active = 0
    calls = []
    state_lock = threading.Lock()

    def fake_input(prompt):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        calls.append(prompt)
        time.sleep(0.02)
        with state_lock:
            active -= 1

    monkeypatch.setattr("builtins.input", fake_input)

    async def run_waiters():
        await asyncio.gather(
            wait_for_manual_unblock("链家(lj)/详情页"),
            wait_for_manual_unblock("贝壳(ke)/首页"),
        )

    asyncio.run(run_waiters())

    assert max_active == 1
    assert len(calls) == 2
    assert "风控暂停" in calls[0]


def test_manual_verify_uses_runtime_platform_check_lock(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: None)

    async def run_waiter():
        runtime_lock = asyncio.Lock()
        set_manual_verify_lock(runtime_lock)
        try:
            await runtime_lock.acquire()
            waiter = asyncio.create_task(wait_for_manual_unblock("链家(lj)/详情页"))
            await asyncio.sleep(0)
            assert waiter.done() is False
            runtime_lock.release()
            await waiter
        finally:
            if runtime_lock.locked():
                runtime_lock.release()
            set_manual_verify_lock(None)

    asyncio.run(run_waiter())


def test_wait_and_reload_continues_only_after_risk_page_recovers(monkeypatch):
    class FakeTab:
        def __init__(self):
            self.target = type("Target", (), {"url": "https://example.test/captcha"})()
            self.reads = 0
            self.activations = 0

        def __await__(self):
            async def ready():
                return self

            return ready().__await__()

        async def get_content(self):
            self.reads += 1
            if self.reads <= 2:
                return "<html><body>请完成验证</body></html>"
            self.target.url = "https://example.test/list"
            return "<html><body>正常房源结果</body></html>"

        async def activate(self):
            self.activations += 1

    async def no_sleep(_seconds):
        return None

    tab = FakeTab()
    monkeypatch.setattr("builtins.input", lambda prompt: None)
    monkeypatch.setattr("app.platforms.base.asyncio.sleep", no_sleep)

    def detect_block(url, html):
        if "captcha" in url or "请完成验证" in html:
            return True, "命中验证码拦截"
        return False, ""

    async def run_wait():
        set_manual_verify_lock(None)
        return await wait_and_reload_after_block(tab, detect_block, "搜索后")

    result = asyncio.run(run_wait())

    assert "正常房源结果" in result
    assert tab.reads == 3
    assert tab.activations == 1
