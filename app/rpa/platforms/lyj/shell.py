# -*- coding: utf-8 -*-
"""乐有家平台适配器。"""

from __future__ import annotations

import logging
from typing import Optional

from app.rpa.platforms.lyj import parser as parsers
from app.rpa.platforms.lyj import collector as lyj_adapter
from app.rpa.core.models import InquiryRequest, PlatformResult, PlatformSession
from app.rpa.core.status import PlatformResultStatus
from app.rpa.platforms.base import PlatformAdapter
from app.rpa.platforms.lyj.constants import START_URL

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

    async def collect(
        self,
        browser,
        session: PlatformSession,
        request: InquiryRequest,
    ):
        # 城市支持检查：不支持则跳过询价，只做保活刷新
        skip = self.check_city_support(request.city, request.request_id)
        if skip is not None:
            try:
                session.page = await session.page.get(self.start_url)
                await session.page
            except Exception as exc:
                log.warning("failed to keepalive lyj page: %s", exc)
            return skip

        # 确保浏览器在目标城市首页（城市不同时先导航过去）
        await self.ensure_city_navigated(session, request.city)

        # URL 白名单直达（方案A）：只有编排层给本平台初始化了挂牌入口才采集。
        listing_url = (request.platform_listing_pages or {}).get(self.code)
        if not listing_url:
            log.info("[%s] 小区无 %s 挂牌入口（白名单未初始化），跳过采集", self.code, self.name)
            return PlatformResult(
                name=self.name,
                status=PlatformResultStatus.NO_DATA,
                reason="无挂牌入口（community_platform_pages 未初始化）",
                request_id=request.request_id,
            )

        result = await lyj_adapter.collect_listing_by_url(
            page=session.page,
            community_name=request.community_name,
            listing_page_url=listing_url,
            request_id=request.request_id,
        )
        try:
            session.page = await lyj_adapter.reset_to_start_page(session.page, request.city)
        except Exception as exc:
            log.warning("failed to reset lyj main page to standby: %s", exc)
        return result

    async def _probe_ready(self, page, html: str) -> tuple[bool, str]:
        """乐有家特有：验证码 + 筛选区。登录检测由基类负责。"""
        if lyj_adapter._is_captcha_html(html):
            return False, "命中验证码拦截"
        if lyj_adapter._is_login_url(page.target.url or ""):
            return False, "当前会话未登录或已失效"
        try:
            await page.select("div.selected-index", timeout=3)
        except Exception:
            return False, "未找到筛选区"
        return True, "READY"

    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        return lyj_adapter.detect_block(url, html)

    def is_no_result(self, html: str) -> bool:
        """统一“空/边界”校验（委托 adapter 平台 marker）。"""
        return lyj_adapter.is_no_result(html)

    def parse_listing_snapshots(self, html: str, base_url: Optional[str] = None) -> list:
        """解析在售快照（委托工程 parser）。"""
        return parsers.parse_listing_snapshots(html, base_url)

    def parse_community_avg_price(self, html: str):
        """解析小区参考均价（委托工程 parser）。"""
        return parsers.parse_community_avg_price(html)
