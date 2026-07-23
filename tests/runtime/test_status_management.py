# -*- coding: utf-8 -*-
"""状态链和任务结果回写的回归测试。"""

import asyncio

from app.core.models import InquiryResult, PlatformResult
from app.core.status import (
    PlatformHealthEvent,
    PlatformHealthStatus,
    PlatformResultStatus,
    ServiceStatus,
    transition_platform_health,
)
from app.runtime import PlatformRuntimeState, RPARuntime


def _runtime_with_ke_ready() -> RPARuntime:
    runtime = RPARuntime()
    runtime.platform_states = {
        "ke": PlatformRuntimeState(
            code="ke",
            name="贝壳",
            start_url="x",
            status=PlatformHealthStatus.READY,
            message="人工确认就绪",
            version=1,
        ),
    }
    runtime._refresh_service_status()
    return runtime


def test_health_transitions_are_centralized():
    assert transition_platform_health(
        PlatformHealthStatus.WAIT_LOGIN,
        PlatformHealthEvent.READY_CHECK_PASSED,
    ) == PlatformHealthStatus.READY
    assert transition_platform_health(
        PlatformHealthStatus.READY,
        PlatformHealthEvent.RESULT_LOGIN_EXPIRED,
    ) == PlatformHealthStatus.WAIT_LOGIN


def test_normal_task_error_does_not_degrade_ready_platform():
    runtime = _runtime_with_ke_ready()

    runtime._apply_platform_results(
        InquiryResult(
            success=False,
            platform_results=[
                PlatformResult(
                    name="贝壳",
                    status=PlatformResultStatus.ERROR,
                    reason="详情页连接已断开",
                )
            ],
        ),
        {"ke": 1},
    )

    state = runtime.platform_states["ke"]
    assert state.status == PlatformHealthStatus.READY
    assert runtime.status == ServiceStatus.READY


def test_stale_login_expired_result_cannot_overwrite_manual_ready():
    runtime = _runtime_with_ke_ready()
    task_versions = {"ke": runtime.platform_states["ke"].version}

    # 人工确认发生在任务完成之前，使任务持有的版本过期。
    runtime._set_platform_health(
        "ke", PlatformHealthEvent.READY_CHECK_PASSED, "人工重新确认就绪"
    )
    runtime._apply_platform_results(
        InquiryResult(
            success=False,
            platform_results=[
                PlatformResult(
                    name="贝壳",
                    status=PlatformResultStatus.LOGIN_EXPIRED,
                    reason="旧任务返回的登录失效",
                )
            ],
        ),
        task_versions,
    )

    assert runtime.platform_states["ke"].status == PlatformHealthStatus.READY
    assert runtime.status == ServiceStatus.READY


def test_current_login_expired_result_requires_manual_recovery():
    runtime = _runtime_with_ke_ready()
    runtime._apply_platform_results(
        InquiryResult(
            success=False,
            platform_results=[
                PlatformResult(
                    name="贝壳",
                    status=PlatformResultStatus.LOGIN_EXPIRED,
                    reason="登录已失效",
                )
            ],
        ),
        {"ke": 1},
    )

    assert runtime.platform_states["ke"].status == PlatformHealthStatus.WAIT_LOGIN
    assert runtime.status == ServiceStatus.WAIT_LOGIN


def test_ready_check_captcha_enters_manual_verify_state():
    class FakeAdapter:
        code = "ke"
        name = "贝壳"

        async def check_ready(self, session):
            return False, "命中验证码拦截(公共HTML标识)"

    runtime = RPARuntime(adapters=[FakeAdapter()])
    runtime.service = type("FakeService", (), {"sessions": {"ke": object()}})()
    runtime.platform_states = {
        "ke": PlatformRuntimeState(
            code="ke",
            name="贝壳",
            start_url="x",
            status=PlatformHealthStatus.WAIT_LOGIN,
            message="等待登录",
        ),
    }

    asyncio.run(runtime.confirm_platform_ready("ke"))

    assert runtime.platform_states["ke"].status == PlatformHealthStatus.WAIT_MANUAL_VERIFY
    assert runtime.status == ServiceStatus.DEGRADED
