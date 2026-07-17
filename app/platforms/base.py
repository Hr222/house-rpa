# -*- coding: utf-8 -*-
"""平台适配器基类。"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
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

    def check_city_support(self, city: str, request_id: Optional[str] = None) -> Optional[PlatformResult]:
        """检查平台是否支持该城市。

        不支持时返回 NO_DATA 结果（含支持城市列表），支持时返回 None。
        各薄壳适配器在 collect() 开头调用本方法，不支持则跳过采集只做保活。
        """
        from app.platforms.city_map import is_city_supported, CITY_MAP
        if is_city_supported(self.code, city):
            return None
        supported = "、".join(sorted(CITY_MAP.get(self.code, {}).keys()))
        reason = f"平台不支持城市「{city}」（支持: {supported}）"
        log.warning("[%s] %s，跳过询价", self.code, reason)
        return PlatformResult(
            name=self.name,
            status="NO_DATA",
            reason=reason,
            request_id=request_id,
        )

    async def ensure_city_navigated(self, session: PlatformSession, city: str) -> None:
        """确保浏览器已导航到目标城市首页。

        在调 adapter.collect 之前调用：如果当前页面不在目标城市域名下，
        先导航到目标城市首页，避免在错误城市搜索导致找不到小区。
        城市相同时跳过，减少不必要的页面刷新。
        """
        from urllib.parse import urlparse
        from app.platforms.city_map import get_start_url

        target_url = get_start_url(self.code, city)
        try:
            current_url = session.page.target.url or ""
        except Exception:
            current_url = ""

        target_domain = urlparse(target_url).netloc   # e.g. "sz.ke.com"
        current_domain = urlparse(current_url).netloc if current_url else ""

        if target_domain == current_domain:
            log.info("[%s] 当前已在城市「%s」(%s)，跳过导航", self.code, city, current_domain)
            return

        log.info("[%s] 城市切换: %s → %s，导航到 %s",
                 self.code, current_domain or "未知", city, target_url)
        page = await session.page.get(target_url)
        await page
        await asyncio.sleep(2)
        session.page = page

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


# 跨平台验证码页共性的可见话术（剥离 script/style 后的正文）
_GENERIC_CAPTCHA_WORDS = (
    "人机验证", "验证码", "验证后继续", "完成验证",
    "滑动验证", "访问过于频繁", "验证码校验", "请输入验证",
)

# 正常业务页必定含有的特征（只要命中一个就不是纯验证码页）
# 覆盖五平台的房源/价格/小区标识
_GENERIC_BUSINESS_WORDS = (
    "元/㎡", "元/平米", "元 ㎡", "sellListContent",
    "在售", "小区均价", "挂牌", "property-content-info-comm-name",
)


def is_generic_captcha_page(html: str) -> bool:
    """通用风控兜底：判断 HTML 是否为验证码拦截页（跨平台共性）。

    判据：正文含验证码话术 且 不含任何业务特征词。
    验证码页没有房源内容，正常页没有验证码话术，两者天然互斥。
    仅在各平台精确 detect_block 都未命中时作为最后一道网调用，
    防止某平台 marker 被删空后整体漏判。
    """
    if not html:
        return False
    # 剥离 script/style 噪音（验证码页 dump 常带 darkreader 注入的大段 CSS）
    clean = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S)
    clean = re.sub(r"<script[^>]*>.*?</script>", "", clean, flags=re.S)
    has_captcha = any(w in clean for w in _GENERIC_CAPTCHA_WORDS)
    has_business = any(w in html for w in _GENERIC_BUSINESS_WORDS)
    return has_captcha and not has_business


def short_circuit_result(
    name: str,
    status: str,
    reason: str,
    request_id: Optional[str],
    started_at: float,
    detail_url: Optional[str] = None,
) -> PlatformResult:
    """构造三类状态短路返回（NO_DATA / WAIT_MANUAL_VERIFY / LOGIN_EXPIRED 等）。

    统一计算 elapsed_seconds，消除各平台 round(time.time()-started_at, 2)
    与 lj 的 _elapsed() 两套写法。各 adapter 的短路返回改调本函数，
    避免 5-6 行模板重复 30+ 次。

    Args:
        name: 平台中文名（如 "贝壳"）。
        status: NO_DATA / WAIT_MANUAL_VERIFY / LOGIN_EXPIRED 等。
        reason: 短路原因（透传给调用方/日志）。
        request_id: 询价请求 id。
        started_at: 采集开始时间戳（time.time()）。
        detail_url: 小区详情 URL（仅部分平台/场景有，默认 None）。
    """
    return PlatformResult(
        name=name,
        status=status,
        reason=reason,
        request_id=request_id,
        elapsed_seconds=round(time.time() - started_at, 2),
        detail_url=detail_url,
    )


# 小区名分期后缀（中英文括号及内容，如 (一期)/(二期)/（一期））
_PHASE_SUFFIX_PATTERN = re.compile(r"\([^)]*\)|（[^）]*）")


def _strip_phase_suffix(name: str) -> str:
    """去掉小区名里的分期括号后缀，如 '星河荣御花苑(一期)' → '星河荣御花苑'。"""
    if not name:
        return ""
    return _PHASE_SUFFIX_PATTERN.sub("", name).strip()


def community_name_match(request_name: str, page_name: str) -> bool:
    """请求小区名与页面小区名是否匹配（容忍分期括号 + 命名差异）。

    房产数据常见噪音：业务系统 "星河荣御花苑(一期)" vs 平台 "星河荣御一期"，
    两边各有增删，单纯子串匹配会漏判导致误报 NO_DATA。策略：
    1. 去掉分期括号 (一期)/(二期)/（一期）；
    2. 双向子串（保留原 in 容差，如 "万科" 匹配 "万科城"）；
    3. 最长公共子串 ≥ 3 字（处理两边各有增删的命名差异，
       如 "星河荣御花苑" vs "星河荣御一期" 公共子串 "星河荣御" 4 字）。

    不去通名（花苑/花园/苑等）——风险大，"汇雅苑" 去 "苑" 会与 "汇雅轩" 误匹配。
    """
    nr = _strip_phase_suffix(request_name)
    np_ = _strip_phase_suffix(page_name)
    if not nr or not np_:
        return False
    # 双向子串（精确 + 容差）
    if nr in np_ or np_ in nr:
        return True
    # 最长公共子串（DP）
    m, n = len(nr), len(np_)
    longest = 0
    # 滚动数组优化空间
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        cur = [0] * (n + 1)
        for j in range(1, n + 1):
            if nr[i - 1] == np_[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > longest:
                    longest = cur[j]
        prev = cur
    return longest >= 3


async def wait_and_reload_after_block(tab, detect_func, label: str = "页面") -> str:
    """详情/成交页被风控时的统一处理：检测 → 等人回车 → 重取，最多 2 次。

    各 adapter 在打开详情/成交 tab 后调用本函数，替代各自手写的
    「detect_block → wait_for_manual_unblock → await tab → sleep → get_content」。

    检测顺序：先用各平台精确 detect_func，再叠通用兜底 is_generic_captcha_page。
    这样即使某平台 marker 被删空，通用兜底仍能识别验证码页。

    Args:
        tab: 详情/成交标签页（nodriver Tab）。
        detect_func: 平台的 detect_block(url, html) -> (bool, str)。
        label: 日志里的页面名称（如 "详情页" / "成交页"）。

    Returns:
        重取后的 html。若 2 次人工后仍被风控，返回最后一次的 html，
        交给调用方走降级（放弃成交，用在售×折扣）。
    """
    def _check(url: str, html: str) -> tuple[bool, str]:
        # 先用各平台精确检测，未命中再叠通用兜底
        blocked, reason = detect_func(url, html)
        if not blocked and is_generic_captcha_page(html):
            blocked, reason = True, "命中验证码拦截(通用兜底)"
        return blocked, reason

    await tab
    html = await tab.get_content()
    for attempt in (1, 2):
        blocked, reason = _check(tab.target.url or "", html)
        if not blocked:
            return html
        log.warning("%s被拦截(%s)，等待人工处理（第 %d 次）", label, reason, attempt)
        await wait_for_manual_unblock()
        await tab
        await asyncio.sleep(3)
        html = await tab.get_content()
    # 2 次仍未解除，返回当前 html，调用方自行判断是否降级
    blocked, reason = _check(tab.target.url or "", html)
    if blocked:
        log.warning("%s经 2 次人工仍未解除风控(%s)，将走降级", label, reason)
    return html


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


async def safe_select_and_click(
    page,
    selector: str,
    *,
    dump_fn,
    dump_name: str,
    detect_fn,
    block_label: str,
    click_label: str = "",
):
    """通用的"安全选择+点击"：找不到元素时 dump 现场 + 风控检测 + 恢复后重试 + 点击。

    封装了翻页时「select → 找不到 → dump → detect_block → wait_and_reload → retry select
    → 仍找不到 → return None → _human_click → 点击失败 → return None」的通用逻辑。
    各平台只需传入 selector + dump/detect 函数 + label，核心逻辑由本函数统一处理。

    Args:
        page: nodriver Tab 对象。
        selector: CSS 选择器（含 page_no 的完整字符串）。
        dump_fn: 各平台的 _dump 函数（导出 debug HTML）。
        dump_name: dump 文件名前缀（如 "ke_page_3_no_button"）。
        detect_fn: 各平台的 detect_block(url, html) -> (bool, str)。
        block_label: 日志/风控 label（如 "第 3 页(翻页前-按钮缺失)"）。
        click_label: 点击日志 label（如 "page 3"）。

    Returns:
        点击成功的 Element；无法找到或点击失败返回 None（优雅停止信号）。
    """
    try:
        element = await page.select(selector, timeout=3)
    except Exception:
        element = None

    if not element:
        await dump_fn(page, dump_name)
        # 检测风控：被风控会替换 DOM 导致找不到按钮
        await wait_and_reload_after_block(page, detect_fn, block_label)
        # 恢复后(或未被风控)重试找按钮
        try:
            element = await page.select(selector, timeout=3)
        except Exception:
            element = None
        if not element:
            log.warning("%s: 找不到按钮(风控恢复后或非风控)，停止翻页", block_label)
            return None

    if not await _human_click(page, element, click_label):
        log.warning("%s: 点击失败，停止翻页", block_label)
        return None

    return element


# 连续空页阈值：达到此值则提前停止翻页，避免无效翻页浪费时间
MAX_CONSECUTIVE_EMPTY_PAGES = 2


def check_empty_listing_page(
    page_no: int,
    page_count: int,
    consecutive_empty: int,
    total_pages: int,
    platform: str = "",
) -> tuple[bool, int]:
    """翻页采集时的空页检测（所有翻页平台共用）。

    解决"total_pages 是旧计数但实际无房源"导致程序静默翻 N 页空数据
    且无任何 warning/error 的问题。

    判定逻辑：
    - 有数据 → 重置计数器为 0，不停止。
    - 首页空且 total_pages > 0 → log.error + 立即停止。
      （total_pages 来自翻页器的旧计数，实际列表区为空 <!-- 无搜索结果 -->，
       翻下去全是空的，对应贝壳"福鑫苑"场景。）
    - 非首页空 → log.warning，计数器 +1。
    - 连续空页 >= MAX_CONSECUTIVE_EMPTY_PAGES → log.warning + 停止。

    Args:
        page_no: 当前页码（从 1 开始）。
        page_count: 当前页解析到的房源条数。
        consecutive_empty: 进入本页前已连续出现的空页数。
        total_pages: 翻页器显示的总页数。
        platform: 平台代码，用于日志标识。

    Returns:
        (should_stop, updated_consecutive_empty)。
    """
    if page_count > 0:
        return False, 0

    # --- 空页 ---
    if page_no == 1 and total_pages > 0:
        log.error(
            "[%s] 第 1 页 0 条房源但 total_pages=%d，"
            "total_pages 可能是旧计数，实际无结果，停止翻页",
            platform, total_pages,
        )
        return True, 1

    consecutive_empty += 1
    log.warning(
        "[%s] 第 %d 页 0 条房源（连续空页 %d/%d）",
        platform, page_no, consecutive_empty, MAX_CONSECUTIVE_EMPTY_PAGES,
    )
    if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGES:
        log.warning(
            "[%s] 连续 %d 页空数据，停止翻页避免浪费时间",
            platform, consecutive_empty,
        )
        return True, consecutive_empty

    return False, consecutive_empty


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

    # 检查档位是否禁用（乐有家: class=disabled; 房天下: style=color:#999 / href=javascript:void(0)）
    try:
        el_class = (await el.get_attribute("class")) or ""
        el_style = (await el.get_attribute("style")) or ""
        el_href = (await el.get_attribute("href")) or ""
        if "disabled" in el_class or "color:#999" in el_style or "javascript:void(0)" in el_href:
            log.info("[%s] 面积档位 %s 已禁用（该区间无房源），返回空", platform_code, target)
            return None
    except Exception:
        pass

    clicked = await _human_click(page, el, f"面积档位 {target}")
    if clicked:
        await page
        await asyncio.sleep(3)
    else:
        log.warning("[%s] 面积档位点击失败: %s", platform_code, target)
    return (matched_lo, matched_hi)
