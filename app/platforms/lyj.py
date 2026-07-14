# -*- coding: utf-8 -*-
"""乐有家平台适配器。"""

from __future__ import annotations

import logging

from app import lyj_adapter
from app.models import InquiryRequest, PlatformSession
from app.platforms.base import PlatformAdapter
from app.platforms.lyj_constants import START_URL

log = logging.getLogger(__name__)


class LyjPlatformAdapter(PlatformAdapter):
    code = "lyj"
    name = "乐有家"
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
        result = await lyj_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area_min=request.area_min,
            area_max=request.area_max,
            request_id=request.request_id,
        )
        try:
            session.page = await lyj_adapter.reset_to_start_page(session.page)
        except Exception as exc:
            log.warning("failed to reset lyj main page to standby: %s", exc)
        return result

    async def check_ready(self, session: PlatformSession) -> tuple[bool, str]:
        return await lyj_adapter.probe_ready(session.page)

    async def keepalive(self, session: PlatformSession) -> tuple[bool, str]:
        return await lyj_adapter.keepalive(session.page)
