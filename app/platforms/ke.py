# -*- coding: utf-8 -*-
"""贝壳平台适配器。"""

from __future__ import annotations

import logging

from app.platforms.adapters import ke as ke_adapter
from app.core.models import InquiryRequest, PlatformSession
from app.platforms.base import PlatformAdapter
from app.platforms.ke_constants import START_URL

log = logging.getLogger(__name__)


class KePlatformAdapter(PlatformAdapter):
    code = "ke"
    name = "贝壳"
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
        # 城市支持检查：不支持则跳过询价，只做保活刷新
        skip = self.check_city_support(request.city, request.request_id)
        if skip is not None:
            try:
                session.page = await session.page.get(self.start_url)
                await session.page
            except Exception as exc:
                log.warning("failed to keepalive ke page: %s", exc)
            return skip

        # 确保浏览器在目标城市首页（城市不同时先导航过去）
        await self.ensure_city_navigated(session, request.city)

        result = await ke_adapter.collect(
            browser=browser,
            main_page=session.page,
            community_name=request.community_name,
            area=request.area,
            request_id=request.request_id,
            city=request.city,
        )
        try:
            session.page = await ke_adapter.reset_to_start_page(session.page, request.city)
        except Exception as exc:
            log.warning("failed to reset ke main page to standby: %s", exc)
        return result

    async def _probe_ready(self, page, html: str) -> tuple[bool, str]:
        """贝壳特有：人机验证 + 搜索框。登录检测由基类 check_ready 负责。"""
        if ke_adapter._is_manual_verify_html(html):
            return False, "命中人机验证，等待人工处理"
        try:
            await ke_adapter._get_search_input(page)
        except Exception:
            return False, "未找到搜索框，页面未就绪"
        return True, "READY"

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        return ke_adapter.detect_block(url, html)
