# -*- coding: utf-8 -*-
"""钉钉群机器人通知。

独立模块，不依赖浏览器 / 平台适配器，可单独测试和调用。
当前只做通知能力，不对接风控流程——验证可行后再接入。

用法::

    from app.utils.dingtalk import send_text, send_markdown
    await send_text("风控拦截，需人工处理")
    await send_markdown("RPA风控告警", "## 贝壳\n被验证码拦截，第3页")

安全设置建议用"自定义关键词"（关键词设为"风控"或"RPA"），
消息内容包含关键词即可发送，无需加签。
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.core import config

log = logging.getLogger(__name__)

# 钉钉 webhook 超时（秒）——通知是辅助功能，不能阻塞主流程太久
_TIMEOUT = 10.0


async def send_text(content: str, at_all: bool = False) -> bool:
    """发送纯文本消息。

    Args:
        content: 消息正文（需包含机器人设置的关键词，否则钉钉会拒绝）。
        at_all: 是否 @所有人。

    Returns:
        True 发送成功，False 发送失败或未配置 webhook。
    """
    webhook = config.DINGTALK_WEBHOOK_URL
    if not webhook:
        log.debug("钉钉 webhook 未配置，跳过通知: %s", content[:50])
        return False

    payload = {
        "msgtype": "text",
        "text": {"content": content},
        "at": {"isAtAll": at_all},
    }
    return await _post(webhook, payload)


async def send_markdown(title: str, text: str) -> bool:
    """发送 markdown 消息（钉钉群里显示为卡片）。

    Args:
        title: 卡片标题（通知列表里预览用）。
        text: markdown 正文（需包含机器人设置的关键词）。

    Returns:
        True 发送成功，False 发送失败或未配置 webhook。
    """
    webhook = config.DINGTALK_WEBHOOK_URL
    if not webhook:
        log.debug("钉钉 webhook 未配置，跳过通知: %s", title)
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
    }
    return await _post(webhook, payload)


async def _post(webhook: str, payload: dict) -> bool:
    """实际发送 POST 请求，失败不抛异常。

    通知是辅助功能，任何异常（网络超时、钉钉拒绝、URL 错误）
    都只打 warning 日志，不影响主流程。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(webhook, json=payload)
        data = resp.json()
    except Exception as exc:
        log.warning("钉钉通知发送失败(网络异常): %s", exc)
        return False

    if data.get("errcode") == 0:
        log.info("钉钉通知发送成功")
        return True

    log.warning("钉钉通知发送失败(errcode=%s, errmsg=%s)",
                data.get("errcode"), data.get("errmsg"))
    return False
