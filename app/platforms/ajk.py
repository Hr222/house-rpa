# -*- coding: utf-8 -*-
"""安居客平台适配器。"""

from __future__ import annotations

import logging

from app.platforms.adapters import ajk as ajk_adapter
from app.core.models import InquiryRequest, PlatformSession
from app.platforms.ajk_constants import START_URL
from app.platforms.base import PlatformAdapter

log = logging.getLogger(__name__)


class AjkPlatformAdapter(PlatformAdapter):
    code = "ajk"
    name = "安居客"
    start_url = START_URL
    requires_login = False

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
        result = await ajk_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area=request.area,
            request_id=request.request_id,
        )
        try:
            session.page = await ajk_adapter.reset_to_start_page(session.page)
        except Exception as exc:
            log.warning("failed to reset ajk main page to standby: %s", exc)
        return result

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        return ajk_adapter.detect_block(url, html)
