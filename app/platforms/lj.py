# -*- coding: utf-8 -*-
"""链家平台适配器。"""

from __future__ import annotations

import logging

from app.platforms.adapters import lj as lj_adapter
from app.core.models import InquiryRequest, PlatformSession
from app.platforms.base import PlatformAdapter
from app.platforms.lj_constants import START_URL

log = logging.getLogger(__name__)


class LjPlatformAdapter(PlatformAdapter):
    code = "lj"
    name = "链家"
    start_url = START_URL

    async def open_session(self, browser) -> PlatformSession:
        page = await browser.get(self.start_url)
        await page
        return PlatformSession(
            code=self.code,
            name=self.name,
            start_url=self.start_url,
            page=page,
            ready=True,
        )

    async def collect(self, browser, session: PlatformSession, request: InquiryRequest):
        result = await lj_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area_min=request.area_min,
            area_max=request.area_max,
            request_id=request.request_id,
        )
        try:
            session.page = await lj_adapter.reset_to_start_page(session.page)
        except Exception as exc:
            log.warning("failed to reset lj main page to standby: %s", exc)
        return result

    async def check_ready(self, session: PlatformSession) -> tuple[bool, str]:
        return await lj_adapter.probe_ready(session.page)

    async def keepalive(self, session: PlatformSession) -> tuple[bool, str]:
        return await lj_adapter.keepalive(session.page)

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        return lj_adapter.detect_block(url, html)
