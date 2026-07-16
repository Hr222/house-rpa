# -*- coding: utf-8 -*-
"""乐有家平台适配器。"""

from __future__ import annotations

import logging

from app.platforms.adapters import lyj as lyj_adapter
from app.core.models import InquiryRequest, PlatformSession
from app.platforms.base import PlatformAdapter
from app.platforms.lyj_constants import START_URL

log = logging.getLogger(__name__)


class LyjPlatformAdapter(PlatformAdapter):
    code = "lyj"
    name = "乐有家"
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
        result = await lyj_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area_min=request.area_min,
            area_max=request.area_max,
            area=request.area,
            request_id=request.request_id,
        )
        try:
            session.page = await lyj_adapter.reset_to_start_page(session.page)
        except Exception as exc:
            log.warning("failed to reset lyj main page to standby: %s", exc)
        return result

    async def _probe_ready(self, page, html: str) -> tuple[bool, str]:
        """乐有家特有：验证码 + 筛选区。登录检测由基类负责。"""
        if lyj_adapter._is_captcha_html(html):
            return False, "命中验证码拦截"
        if lyj_adapter._is_login_html(html):
            return False, "当前会话未登录或已失效"
        try:
            await page.select("div.selected-index", timeout=3)
        except Exception:
            return False, "未找到筛选区"
        return True, "READY"

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        return lyj_adapter.detect_block(url, html)
