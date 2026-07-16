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
        result = await lj_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area_min=request.area_min,
            area_max=request.area_max,
            area=request.area,
            request_id=request.request_id,
        )
        try:
            session.page = await lj_adapter.reset_to_start_page(session.page)
        except Exception as exc:
            log.warning("failed to reset lj main page to standby: %s", exc)
        return result

    async def _probe_ready(self, page, html: str) -> tuple[bool, str]:
        """链家特有：域名 + 搜索框 + 房源列表。登录检测由基类负责。"""
        current_url = page.target.url or ""
        if "lianjia.com" not in current_url:
            return False, f"未在链家域名，当前 URL: {current_url}"
        try:
            inp = await page.select("#searchInput", timeout=3)
            if inp is None:
                return False, "未找到搜索框"
        except Exception:
            return False, "未找到搜索框"
        if "sellListContent" not in (html or ""):
            return False, "页面无房源列表"
        return True, "READY"

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        return lj_adapter.detect_block(url, html)
