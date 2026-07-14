# -*- coding: utf-8 -*-
"""平台适配器基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.models import InquiryRequest, PlatformResult, PlatformSession


class PlatformAdapter(ABC):
    """所有平台统一实现的接口。"""

    code: str
    name: str
    start_url: str

    @abstractmethod
    async def open_session(self, browser) -> PlatformSession:
        """打开平台常驻页并返回会话对象。"""

    @abstractmethod
    async def collect(
        self,
        browser,
        session: PlatformSession,
        request: InquiryRequest,
    ) -> PlatformResult:
        """执行单次采集。"""

    @abstractmethod
    async def check_ready(self, session: PlatformSession) -> tuple[bool, str]:
        """检测平台当前是否已登录且可接单。"""

    async def keepalive(self, session: PlatformSession) -> tuple[bool, str]:
        """执行轻量保活；默认直接复用 ready 检查。"""
        return await self.check_ready(session)

    @abstractmethod
    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        """检测当前页面是否被风控/登录拦截。

        每个平台各自实现自己的风控规则（标记词、URL 特征等不同）。
        返回 (是否被拦, 原因)：
        - 命中验证码/人机验证 → (True, "命中验证码拦截")
        - 命中登录页         → (True, "命中登录页")
        - 正常               → (False, "")
        """


async def wait_for_manual_unblock():
    """被风控 / 登录拦截时，暂停等待人工处理。

    这是所有平台共用的行为（终端提示 + 等回车），不需要各平台各自实现。
    """
    import asyncio
    prompt = (
        "\n⚠ 检测到风控 / 登录拦截，请在浏览器完成人工处理后，"
        "回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)
