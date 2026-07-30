# -*- coding: utf-8 -*-
"""行舟深房平台薄壳适配器。"""

from __future__ import annotations

import logging

from app.core.models import InquiryRequest, PlatformResult, PlatformSession
from app.platforms import xzsfbj_constants as constants
from app.platforms.adapters.xzsfbj import XzsfbjApiAdapter
from app.platforms.base import PlatformAdapter

log = logging.getLogger(__name__)


class XzsfbjPlatformAdapter(PlatformAdapter):
    """以 WMPF token + HTTP 接口为数据源的行舟深房适配器。"""

    code = "xzsfbj"
    name = "行舟深房"
    start_url = constants.START_URL
    # 平台没有网页登录页；token 由微信小程序会话捕获，不能走基类 HTML 登录检测。
    requires_login = False
    ready_confirmation_hint = (
        "无需网页登录；请确认 .env、WMPF 桥依赖和微信小程序已准备，"
        "回到终端按回车确认。首次采集时会自动捕获 token。"
    )
    ready_message = "依赖已就绪；无需网页登录，首次采集时自动捕获 token"

    def __init__(self) -> None:
        self._api = XzsfbjApiAdapter()

    async def open_session(self, browser, new_tab: bool = False) -> PlatformSession:
        # Runtime 要求每个平台有一个可 activate 的常驻页；该页只承载生命周期，
        # 行舟深房实际采集完全走 WMPF/HTTP，不在网页上导航或抓取。
        page = await browser.get(constants.START_URL, new_tab=new_tab)
        await page
        return PlatformSession(
            code=self.code,
            name=self.name,
            start_url=self.start_url,
            page=page,
            ready=True,
        )

    async def check_ready(self, session: PlatformSession) -> tuple[bool, str]:
        """检查本地索引和桥依赖；不尝试捕获 token，避免启动时打断微信。"""
        ready, reason = self._api.dependencies_ready()
        return ready, self.ready_message if ready else reason

    async def keepalive(self, session: PlatformSession) -> tuple[bool, str]:
        """接口平台无网页保活动作，仅复用依赖检查。"""
        ready, reason = self._api.dependencies_ready()
        return ready, self.ready_message if ready else reason

    async def collect(
        self,
        browser,
        session: PlatformSession,
        request: InquiryRequest,
    ) -> PlatformResult:
        skip = self.check_city_support(request.city, request.request_id)
        if skip is not None:
            # 行舟深房的城市由 xqData.json 的 regionId 决定，没有网页城市首页可导航。
            log.info("[%s] 不支持城市时仅保留常驻 about:blank 会话", self.code)
            return skip
        return await self._api.collect(request)

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        """WMPF 接口风控在 API 响应中判定，常驻 about:blank 不参与网页检测。"""
        return False, ""
