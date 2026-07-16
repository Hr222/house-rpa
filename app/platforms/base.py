# -*- coding: utf-8 -*-
"""平台适配器基类。"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from abc import ABC, abstractmethod
from typing import Optional

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


async def _human_click(page, element, label: str) -> bool:
    """真人节奏点击元素（所有平台共用）。

    优先 JS click（精确点击目标元素，避免坐标偏移被悬浮客服/广告拦截），
    失败则降级为 mouse_click。随机间隔模拟真人操作。
    """
    if not element:
        return False

    try:
        await element.scroll_into_view()
    except Exception:
        pass

    await asyncio.sleep(random.uniform(0.2, 0.5))
    last_error = None
    for clicker in ("js", "mouse"):
        try:
            if clicker == "js":
                await element.click()
            else:
                try:
                    await element.mouse_move()
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                except Exception:
                    pass
                await element.mouse_click()
            await page
            await asyncio.sleep(random.uniform(0.5, 1.0))
            return True
        except Exception as exc:
            last_error = exc

    log.warning("%s click failed: %s", label, last_error)
    return False


async def click_area_segment(
    page, area: float, parse_func, platform_code: str
) -> Optional[tuple[float, float]]:
    """从结果页面积筛选区动态读取档位，点击匹配 area 的档位链接（所有平台共用）。

    档位因城市而异，不能硬编码，必须从 HTML 实时读取。
    匹配规则：左闭右开（area >= min 且 area < max）。
    各平台档位都是 <a> 链接，点击即筛选，不需要填输入框或点确定。

    Args:
        page: 结果页
        area: 精确面积（必填）。
        parse_func: 平台 parser 的 parse_area_segments 函数
        platform_code: 平台代码，用于日志

    Returns:
        匹配到的面积区间 (min, max)，可用于后续成交记录筛选。
        返回 None 表示未能读取到档位（页面结构变化等异常）。
    """
    html = await page.get_content()
    segments = parse_func(html)
    if not segments:
        log.warning("[%s] 未从页面读到面积档位，跳过面积筛选", platform_code)
        return None

    log.info("[%s] 读到面积档位: %s", platform_code,
             [(t, lo, hi) for t, lo, hi in segments])

    # 左闭右开匹配
    target = None
    matched_lo = matched_hi = 0.0
    for text, lo, hi in segments:
        if lo <= area < hi:
            target = text
            matched_lo, matched_hi = lo, hi
            break

    if target is None:
        target = segments[-1][0]
        matched_lo, matched_hi = segments[-1][1], segments[-1][2]
        log.info("[%s] 面积 %.1f 超出档位范围，取末档 %s (%.0f~%.0f)",
                 platform_code, area, target, matched_lo, matched_hi)
    else:
        log.info("[%s] 面积 %.1f 匹配档位 %s (%.0f~%.0f)",
                 platform_code, area, target, matched_lo, matched_hi)

    # 点击对应的档位链接（通过文本定位）
    try:
        el = await page.find(target, timeout=4)
    except Exception:
        el = None
    if el is None:
        log.warning("[%s] 未找到面积档位按钮: %s", platform_code, target)
        return None

    clicked = await _human_click(page, el, f"面积档位 {target}")
    if clicked:
        await page
        await asyncio.sleep(3)
    else:
        log.warning("[%s] 面积档位点击失败: %s", platform_code, target)
    return (matched_lo, matched_hi)
