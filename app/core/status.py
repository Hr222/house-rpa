# -*- coding: utf-8 -*-
"""Central status definitions and platform health transitions."""

from enum import StrEnum


class ServiceStatus(StrEnum):
    BOOTING = "BOOTING"
    WAIT_LOGIN = "WAIT_LOGIN"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


class PlatformHealthStatus(StrEnum):
    INIT = "INIT"
    WAIT_LOGIN = "WAIT_LOGIN"
    READY = "READY"
    WAIT_MANUAL_VERIFY = "WAIT_MANUAL_VERIFY"
    ERROR = "ERROR"


class PlatformResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NO_DATA = "NO_DATA"
    NO_MATCHING_AREA = "NO_MATCHING_AREA"
    WAIT_MANUAL_VERIFY = "WAIT_MANUAL_VERIFY"
    LOGIN_EXPIRED = "LOGIN_EXPIRED"
    ERROR = "ERROR"


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PlatformHealthEvent(StrEnum):
    INITIALIZE = "INITIALIZE"
    READY_CHECK_PASSED = "READY_CHECK_PASSED"
    READY_CHECK_FAILED = "READY_CHECK_FAILED"
    READY_CHECK_MANUAL_VERIFY = "READY_CHECK_MANUAL_VERIFY"
    KEEPALIVE_READY = "KEEPALIVE_READY"
    KEEPALIVE_LOGIN_REQUIRED = "KEEPALIVE_LOGIN_REQUIRED"
    KEEPALIVE_MANUAL_VERIFY = "KEEPALIVE_MANUAL_VERIFY"
    RESULT_LOGIN_EXPIRED = "RESULT_LOGIN_EXPIRED"
    RESULT_MANUAL_VERIFY = "RESULT_MANUAL_VERIFY"
    PLATFORM_ERROR = "PLATFORM_ERROR"


_HEALTH_TRANSITIONS = {
    PlatformHealthEvent.INITIALIZE: PlatformHealthStatus.WAIT_LOGIN,
    PlatformHealthEvent.READY_CHECK_PASSED: PlatformHealthStatus.READY,
    PlatformHealthEvent.READY_CHECK_FAILED: PlatformHealthStatus.WAIT_LOGIN,
    PlatformHealthEvent.READY_CHECK_MANUAL_VERIFY: PlatformHealthStatus.WAIT_MANUAL_VERIFY,
    PlatformHealthEvent.KEEPALIVE_READY: PlatformHealthStatus.READY,
    PlatformHealthEvent.KEEPALIVE_LOGIN_REQUIRED: PlatformHealthStatus.WAIT_LOGIN,
    PlatformHealthEvent.KEEPALIVE_MANUAL_VERIFY: PlatformHealthStatus.WAIT_MANUAL_VERIFY,
    PlatformHealthEvent.RESULT_LOGIN_EXPIRED: PlatformHealthStatus.WAIT_LOGIN,
    PlatformHealthEvent.RESULT_MANUAL_VERIFY: PlatformHealthStatus.WAIT_MANUAL_VERIFY,
    PlatformHealthEvent.PLATFORM_ERROR: PlatformHealthStatus.ERROR,
}


def transition_platform_health(
    current: str | PlatformHealthStatus,
    event: PlatformHealthEvent,
) -> PlatformHealthStatus:
    """Return the health state produced by a platform event.

    Task result statuses are intentionally not accepted here. A normal task
    failure is not evidence that the platform itself became unavailable.
    """
    if event not in _HEALTH_TRANSITIONS:
        raise ValueError(f"unsupported platform health event: {event}")
    return _HEALTH_TRANSITIONS[event]


def platform_result_updates_health(status: str | PlatformResultStatus) -> bool:
    """Whether a collection result is allowed to change platform health."""
    return status in {
        PlatformResultStatus.LOGIN_EXPIRED,
        PlatformResultStatus.WAIT_MANUAL_VERIFY,
    }
