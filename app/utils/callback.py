# -*- coding: utf-8 -*-
"""结果回调推送。

采集完成后主动把结果 POST 给客户端（{CALLBACK_URL}/{task_id}），
作为主机制取代客户端轮询。本模块只负责"尽力推送"，网络异常不外抛：
- 重试若干次（递增延迟）；
- 全部失败则记 warning，不抛异常，不影响任务本身的结果落库。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.core import config

log = logging.getLogger(__name__)


async def notify_result(
    callback_url: Optional[str],
    task_id: str,
    payload: dict,
    *,
    retries: int = 3,
    client_factory=None,
) -> bool:
    """把询价结果 POST 到 {callback_url}/{task_id}。

    Args:
        callback_url: 回调基址。为空则跳过，直接返回 False。
        task_id: 任务 ID，拼到 URL 末尾，同时 body 里也会带。
        payload: 结果 JSON。
        retries: 最大尝试次数（含首次）。
        client_factory: 可选，构造 httpx.AsyncClient 的工厂（测试注入用）。
            为 None 时走真实 httpx.AsyncClient(timeout=...)。

    Returns:
        True 表示至少有一次成功（HTTP 2xx）；False 表示未配置或全部失败。
        任何异常都被吞掉，绝不外抛。
    """
    if not callback_url:
        return False

    target = callback_url.rstrip("/") + f"/{task_id}"
    timeout = config.REQUEST_TIMEOUT
    last_error: Optional[str] = None
    make_client = client_factory or (lambda **kw: httpx.AsyncClient(**kw))

    for attempt in range(1, retries + 1):
        try:
            async with make_client(timeout=timeout) as client:
                resp = await client.post(target, json=payload)
            if 200 <= resp.status_code < 300:
                log.info("回调成功: %s (attempt %d)", target, attempt)
                return True
            last_error = f"HTTP {resp.status_code}"
            log.warning(
                "回调非 2xx: %s -> %s (attempt %d/%d)",
                target, last_error, attempt, retries,
            )
        except Exception as exc:
            last_error = str(exc)
            log.warning(
                "回调异常: %s -> %s (attempt %d/%d)",
                target, last_error, attempt, retries,
            )

        if attempt < retries:
            await asyncio.sleep(0.5 * attempt)  # 0.5s, 1s, 1.5s ...

    log.error("回调最终失败: %s, 最近原因: %s", target, last_error)
    return False
