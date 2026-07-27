# -*- coding: utf-8 -*-
"""集中定义服务、平台、任务状态及其展示文案。

状态分为服务状态、平台健康状态、平台结果状态和任务状态四类。
平台健康状态描述平台当前能否继续采集，平台结果状态只描述一次询价结果，
两者不能互相替代；普通的无数据、面积不匹配和采集异常也不应直接改变平台健康状态。
"""

from enum import StrEnum


class ServiceStatus(StrEnum):
    """整个 RPA 服务的运行状态。"""

    BOOTING = "BOOTING"
    WAIT_LOGIN = "WAIT_LOGIN"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


class PlatformHealthStatus(StrEnum):
    """平台当前是否具备继续采集的条件。"""

    INIT = "INIT"
    WAIT_LOGIN = "WAIT_LOGIN"
    READY = "READY"
    WAIT_MANUAL_VERIFY = "WAIT_MANUAL_VERIFY"
    ERROR = "ERROR"


class PlatformResultStatus(StrEnum):
    """某次询价在单个平台上的采集结果。"""

    SUCCESS = "SUCCESS"
    NO_DATA = "NO_DATA"
    NO_MATCHING_AREA = "NO_MATCHING_AREA"
    WAIT_MANUAL_VERIFY = "WAIT_MANUAL_VERIFY"
    LOGIN_EXPIRED = "LOGIN_EXPIRED"
    ERROR = "ERROR"


class TaskStatus(StrEnum):
    """单个询价任务的生命周期状态。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PlatformHealthEvent(StrEnum):
    """驱动平台健康状态变化的事件。

    只有明确表示登录失效、人工验证或平台异常的事件才会影响平台健康状态。
    SUCCESS、NO_DATA、NO_MATCHING_AREA 等任务结果不会在这里建模为事件。
    """

    READY_CHECK_PASSED = "READY_CHECK_PASSED"
    READY_CHECK_FAILED = "READY_CHECK_FAILED"
    READY_CHECK_MANUAL_VERIFY = "READY_CHECK_MANUAL_VERIFY"
    KEEPALIVE_READY = "KEEPALIVE_READY"
    KEEPALIVE_LOGIN_REQUIRED = "KEEPALIVE_LOGIN_REQUIRED"
    KEEPALIVE_MANUAL_VERIFY = "KEEPALIVE_MANUAL_VERIFY"
    RESULT_LOGIN_EXPIRED = "RESULT_LOGIN_EXPIRED"
    RESULT_MANUAL_VERIFY = "RESULT_MANUAL_VERIFY"


_HEALTH_TRANSITIONS = {
    PlatformHealthEvent.READY_CHECK_PASSED: PlatformHealthStatus.READY,
    PlatformHealthEvent.READY_CHECK_FAILED: PlatformHealthStatus.WAIT_LOGIN,
    PlatformHealthEvent.READY_CHECK_MANUAL_VERIFY: PlatformHealthStatus.WAIT_MANUAL_VERIFY,
    PlatformHealthEvent.KEEPALIVE_READY: PlatformHealthStatus.READY,
    PlatformHealthEvent.KEEPALIVE_LOGIN_REQUIRED: PlatformHealthStatus.WAIT_LOGIN,
    PlatformHealthEvent.KEEPALIVE_MANUAL_VERIFY: PlatformHealthStatus.WAIT_MANUAL_VERIFY,
    PlatformHealthEvent.RESULT_LOGIN_EXPIRED: PlatformHealthStatus.WAIT_LOGIN,
    PlatformHealthEvent.RESULT_MANUAL_VERIFY: PlatformHealthStatus.WAIT_MANUAL_VERIFY,
}


# 对外序列化和运行日志使用的中文文案统一放在这里，避免 Runtime、Excel
# 各自维护一份映射后出现同一状态显示不一致。
SERVICE_STATUS_TEXT = {
    ServiceStatus.BOOTING: "启动中",
    ServiceStatus.WAIT_LOGIN: "等待登录",
    ServiceStatus.READY: "已就绪",
    ServiceStatus.DEGRADED: "部分降级",
    ServiceStatus.STOPPING: "已停止",
}

TASK_STATUS_TEXT = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.RUNNING: "执行中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.FAILED: "失败",
}

PLATFORM_HEALTH_STATUS_TEXT = {
    PlatformHealthStatus.INIT: "初始化中",
    PlatformHealthStatus.WAIT_LOGIN: "等待登录",
    PlatformHealthStatus.READY: "已就绪",
    PlatformHealthStatus.WAIT_MANUAL_VERIFY: "等待人工验证",
    PlatformHealthStatus.ERROR: "平台异常",
}

PLATFORM_RESULT_STATUS_TEXT = {
    PlatformResultStatus.SUCCESS: "成功",
    PlatformResultStatus.NO_DATA: "无数据",
    PlatformResultStatus.NO_MATCHING_AREA: "面积不匹配",
    PlatformResultStatus.WAIT_MANUAL_VERIFY: "等待人工验证",
    PlatformResultStatus.LOGIN_EXPIRED: "登录已失效",
    PlatformResultStatus.ERROR: "本次采集异常",
}

# 操作日志可能同时出现平台结果状态和任务失败状态，供 Excel 分析统一展示。
OPERATION_STATUS_TEXT = {
    **PLATFORM_RESULT_STATUS_TEXT,
    TaskStatus.FAILED: "失败",
}

# 决策分支不是平台状态，但也是跨运行时、回调和 Excel 的统一展示字段。
BRANCH_TEXT = {
    "NO_DATA": "无可用数据",
    "NO_MATCHING_AREA": "无匹配面积房源",
    "FAILED": "无可用结果",
    "WEIGHTED_MEDIAN": "主要价格落点中位数折扣",
    "WEIGHTED_MEDIAN_MULTI": "多个高频价格落点，取最低价格峰中位数，不打折",
}


def transition_platform_health(
    event: PlatformHealthEvent,
) -> PlatformHealthStatus:
    """根据平台健康事件返回目标状态。

    任务结果状态不会直接传入这里；普通任务失败也不能据此认定平台不可用。
    普通任务结果由 Runtime 另行记录，不通过健康事件改变平台状态。
    """
    if event not in _HEALTH_TRANSITIONS:
        raise ValueError(f"unsupported platform health event: {event}")
    return _HEALTH_TRANSITIONS[event]


def platform_result_updates_health(status: str | PlatformResultStatus) -> bool:
    """判断平台结果是否允许回写平台健康状态。"""
    return status in {
        PlatformResultStatus.LOGIN_EXPIRED,
        PlatformResultStatus.WAIT_MANUAL_VERIFY,
    }
