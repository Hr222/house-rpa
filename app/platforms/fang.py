# -*- coding: utf-8 -*-
"""房天下平台适配器。"""

from __future__ import annotations

import logging

from app.platforms.adapters import fang as fang_adapter
from app.core.models import InquiryRequest, PlatformSession
from app.platforms.base import PlatformAdapter
from app.platforms.fang_constants import START_URL

log = logging.getLogger(__name__)


class FangPlatformAdapter(PlatformAdapter):
    code = "fang"
    name = "房天下"
    start_url = START_URL

    async def open_session(self, browser, new_tab=False) -> PlatformSession:
        page = await browser.get(self.start_url, new_tab=new_tab)
        await page
        return PlatformSession(
            code=self.code,
            name=self.name,
            start_url=self.start_url,
            page=page,
            ready=True,
        )

    async def collect(self, browser, session: PlatformSession, request: InquiryRequest):
        result = await fang_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area=request.area,
            request_id=request.request_id,
        )
        try:
            session.page = await fang_adapter.reset_to_start_page(session.page)
        except Exception as exc:
            log.warning("failed to reset fang main page to standby: %s", exc)
        return result

    async def _probe_ready(self, page, html: str) -> tuple[bool, str]:
        """房天下特有：验证码 + 搜索框。登录检测由基类负责。"""
        if fang_adapter._is_captcha_html(html):
            return False, "命中验证码拦截"
        if fang_adapter._is_login_html(html):
            return False, "当前会话未登录或已失效"
        try:
            await page.select("body", timeout=10)
        except Exception:
            return False, "页面未就绪"
        return True, "READY"

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        return fang_adapter.detect_block(url, html)
