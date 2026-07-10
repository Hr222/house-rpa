# -*- coding: utf-8 -*-
"""平台适配器基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import InquiryRequest, PlatformResult, PlatformSession


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
