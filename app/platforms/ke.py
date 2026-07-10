# -*- coding: utf-8 -*-
"""贝壳平台适配器。"""

from __future__ import annotations

from app import ke_adapter
from app.models import InquiryRequest, PlatformSession
from app.platforms.base import PlatformAdapter
from app.platforms.ke_constants import START_URL


class KePlatformAdapter(PlatformAdapter):
    code = "ke"
    name = "贝壳"
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
        return await ke_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area_min=request.area_min,
            area_max=request.area_max,
            request_id=request.request_id,
        )

    async def check_ready(self, session: PlatformSession) -> tuple[bool, str]:
        return await ke_adapter.probe_ready(session.page)

    async def keepalive(self, session: PlatformSession) -> tuple[bool, str]:
        return await ke_adapter.keepalive(session.page)
