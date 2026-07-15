# -*- coding: utf-8 -*-
"""平台适配器基类。"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod

from app.core.models import InquiryRequest, PlatformResult, PlatformSession

log = logging.getLogger(__name__)

# 未登录时页面的登录链接（a标签文本为"登录" + href含login/passport且非logout）
_LOGIN_HREF_PATTERN = re.compile(
    r'''<a\b[^>]*href=["'][^"']*(?:login|passport)(?![^"']*\bout\b)[^"']*["'][^>]*>\s*(?:登录|登入|Login|Sign\s*in)\s*</a>''',
    re.IGNORECASE,
)
# 兜底：href为javascript:或无href的登录按钮（如房天下 sfHeadUsername）
_LOGIN_BTN_PATTERN = re.compile(
    r'''<a\b[^>]*>\s*(?:登录|登入|Login|Sign\s*in)\s*</a>''',
    re.IGNORECASE,
)


class PlatformAdapter(ABC):
    """所有平台统一实现的接口。"""

    code: str
    name: str
    start_url: str
    requires_login: bool = True  # 子类可设为 False（如安居客无需登录）

    _LOGIN_POSITIVE_MARKERS = ("退出", "退出登录")  # 已登录后页面必定包含的标识

    @abstractmethod
    async def open_session(self, browser, new_tab: bool = False) -> PlatformSession:
        """打开平台常驻页并返回会话对象。"""

    @abstractmethod
    async def collect(
        self,
        browser,
        session: PlatformSession,
        request: InquiryRequest,
    ) -> PlatformResult:
        """执行单次采集。"""

    async def check_ready(self, session: PlatformSession) -> tuple[bool, str]:
        """统一就绪检测：基类负责登录检测，子类只需实现 _probe_page。"""
        try:
            page = session.page
            # 最多重试 2 次（WebSocket 偶尔会断，重连即可）
            html = ""
            for attempt in range(3):
                try:
                    await page.select("body", timeout=8)
                    await page
                    html = await page.get_content()
                    break
                except Exception as exc:
                    err_str = str(exc)
                    if "close frame" in err_str or "websocket" in err_str.lower():
                        log.warning("[%s] WebSocket 断连，重试 (%d/2): %s", self.code, attempt + 1, err_str[:80])
                        await asyncio.sleep(2)
                        continue
                    return False, f"页面不可用: {err_str}"
            else:
                return False, "WebSocket 重连失败，请重启服务"
            await self._dump_if_debug(page, f"{self.code}_check_ready")
        except Exception as exc:
            return False, f"页面不可用: {exc}"

        # 登录检测（双重：正向"退出"标识 + 反向"登录"a标签兜底）
        if self.requires_login and not self._is_logged_in(html):
            return False, "未检测到已登录标识（页面含登录链接或缺少'退出'）"

        # 平台特有检测（搜索框、筛选区等）
        return await self._probe_ready(session.page, html)

    @staticmethod
    async def _dump_if_debug(page, name: str):
        """调试模式下导出页面 HTML。"""
        try:
            from app.utils.debug_utils import is_debug_mode, dump_html
            if is_debug_mode():
                await dump_html(page, name)
        except Exception:
            pass

    async def _probe_ready(self, page, html: str) -> tuple[bool, str]:
        """平台特有就绪检测（子类实现，默认通过）。"""
        return True, "READY"

    @classmethod
    def _is_logged_in(cls, html: str) -> bool:
        """登录检测：正向查'退出'标识 + 反向查'登录'链接兜底。

        出处：dump验证 — 已登录页面必定有"退出"或"退出登录"文字。
              兜底：未登录页面有 <a>登录</a> 或 <a href="...login...">。
              房天下特殊：退出 div 有 style="display:none" 但未登录时仍在 DOM，
              <a id="sfHeadUsername">登录</a> 反向检测兜底。
        """
        # 正向：必须含"退出"标识
        if not any(m in (html or "") for m in cls._LOGIN_POSITIVE_MARKERS):
            return False
        # 反向兜底：不应含登录链接
        # 1) href含login/passport + 文本"登录"（主检测，排除logout）
        if _LOGIN_HREF_PATTERN.search(html or ""):
            return False
        # 2) 兜底：任何<a>登录</a>，但排除链家/乐有家模板（class含user/tel/btn）
        for m in _LOGIN_BTN_PATTERN.finditer(html or ""):
            tag = m.group()
            if "login-user-btn" in tag or "login-user-tel-btn" in tag or "loginbtn" in tag:
                continue
            return False
        return True

    async def keepalive(self, session: PlatformSession) -> tuple[bool, str]:
        """执行轻量保活；默认直接复用 check_ready。"""
        return await self.check_ready(session)

    @abstractmethod
    def detect_block(self, url: str, html: str) -> tuple[bool, str]:
        """检测当前页面是否被风控/登录拦截。"""


async def human_linger(page, page_no: int, linger_seconds: float = None):
    """翻页后模拟停留，所有平台共用。"""
    from app.core import config
    secs = linger_seconds if linger_seconds is not None else config.PAGE_LINGER_SECONDS
    log.info("lingering on result page %s for %.1fs", page_no, secs)
    await asyncio.sleep(secs)


async def wait_for_manual_unblock():
    """被风控 / 登录拦截时，暂停等待人工处理。

    这是所有平台共用的行为（终端提示 + 等回车），不需要各平台各自实现。
    """
    import asyncio
    prompt = (
        "\n⚠ 检测到风控 / 登录拦截，请在浏览器完成人工处理后，"
        "回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)
