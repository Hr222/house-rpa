# -*- coding: utf-8 -*-
"""房天下平台适配器。"""

from __future__ import annotations

import logging

from app import fang_adapter
from app.models import InquiryRequest, PlatformSession
from app.platforms.base import PlatformAdapter
from app.platforms.fang_constants import START_URL

log = logging.getLogger(__name__)


class FangPlatformAdapter(PlatformAdapter):
    code = "fang"
    name = "房天下"
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
        result = await fang_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area_min=request.area_min,
            area_max=request.area_max,
            request_id=request.request_id,
        )
        try:
            session.page = await fang_adapter.reset_to_start_page(session.page)
        except Exception as exc:
            log.warning("failed to reset fang main page to standby: %s", exc)
        return result

    async def check_ready(self, session: PlatformSession) -> tuple[bool, str]:
        return await fang_adapter.probe_ready(session.page)

    async def keepalive(self, session: PlatformSession) -> tuple[bool, str]:
        return await fang_adapter.keepalive(session.page)
