# -*- coding: utf-8 -*-
"""链家 MVP 测试脚本。

逐步迭代：在同一脚本里一步步验证链家链路，不另建脚本。
当前覆盖：第1步 首页打开 + 探测拦截。

链家二手房：https://{城市拼音缩写}.lianjia.com/ershoufang/
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Optional

import nodriver as uc

import config
from app.debug_utils import dump_html as shared_dump_html
from app.debug_utils import set_debug_mode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("lj-mvp-test")

# 链家深圳二手房首页
START_URL = "https://sz.lianjia.com/ershoufang/"

# 固定测试场景：与贝壳/安居客/房天下脚本一致
COMMUNITY_NAME = "绿景虹湾"
AREA_MIN = 70
AREA_MAX = 90


# ============================================================
# 通用：HTML 导出 / 拦截判定 / 人工等待
# ============================================================

async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def is_captcha_url(url: str) -> bool:
    """链家验证码拦截页 URL 特征（具体待 dump 核对后收敛）。"""
    url = (url or "").lower()
    return "captcha" in url or "verifycode" in url or "antibot" in url or "antispam" in url


def is_captcha_html(html: str) -> bool:
    markers = (
        "请输入验证码",
        "验证后继续访问",
        "请完成验证",
        "滑动验证",
    )
    return any(marker in html for marker in markers)


def is_login_html(html: str) -> bool:
    markers = (
        "请输入手机号",
        "请输入密码",
        "手机快捷登录",
        "扫码登录",
    )
    return any(marker in html for marker in markers)


async def wait_for_manual_login():
    prompt = (
        "\n请在打开的浏览器里手动完成验证码 / 登录。"
        "\n完成后回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


async def wait_for_manual_close():
    prompt = (
        "\n浏览器将保持打开，方便你现场查看。"
        "\n看完后回到终端按回车结束脚本...\n"
    )
    await asyncio.to_thread(input, prompt)


# ============================================================
# 第2步：页面交互辅助
# ============================================================

async def is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def human_click(page, element, label: str) -> bool:
    if not element:
        return False
    try:
        await element.scroll_into_view()
    except Exception:
        pass
    try:
        await element.mouse_move()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    last_error = None
    for clicker in ("mouse", "js"):
        try:
            if clicker == "mouse":
                await element.mouse_click()
            else:
                await element.click()
            await page
            await asyncio.sleep(1.0)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s click failed: %s", label, last_error)
    return False


# ============================================================
# 第2步：搜索小区
#    链家搜索 URL 格式：/ershoufang/rs{小区名}/
#    页面上的 search_input + 回车不可靠（AJAX 搜索 URL 不变），改用 URL 直接导航。
# ============================================================

async def _search_community(page, community_name: str) -> str:
    """搜索小区：在已登录的首页填搜索框 + 点按钮提交。

    URL 导航会跳转到登录页（登录态不跨路径），所以必须在登录后的首页操作。
    用 evaluate 直接 JS 点击按钮，比 send_keys 回车更可靠。
    """
    # 确保在首页且已登录
    try:
        inp = await page.select("#searchInput", timeout=3)
    except Exception:
        inp = None
    if inp is None:
        raise RuntimeError("未找到搜索框 #searchInput")

    if not await human_click(page, inp, "search input"):
        raise RuntimeError("搜索框未能成功点击")

    try:
        await inp.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.5)
    await inp.send_keys(community_name)
    await page
    await asyncio.sleep(1.0)

    # JS 点击搜索按钮（链家和贝壳同代码库）
    result = await page.evaluate(
        """
        (() => {
            const btn = document.querySelector('i.btn-search') || document.querySelector('.btn-search');
            if (btn) { btn.click(); return 'clicked'; }
            return 'no-btn';
        })()
        """,
        return_by_value=True,
    )
    log.info("[2] JS 点击搜索按钮结果: %s", result)
    await page
    await asyncio.sleep(3)

    return await page.get_content()


# ============================================================
# 第3步：面积筛选（链家需先"更多选项"→"更多及自定义"→点面积档位）
# ============================================================

async def apply_area_filter(page, area_min, area_max):
    """链家面积筛选：智能展开 → 面积区"更多及自定义" → 点档位。

    链家首页：div.more.btn-more = "更多选项"（需点击展开）
    搜索结果页：div.more.btn-more = "收起选项"（已展开，不能点！）
    """
    # 1. 智能处理全局展开：只有"更多选项"才点，"收起选项"跳过
    try:
        more_btn = await page.select("div.more.btn-more", timeout=3)
    except Exception:
        more_btn = None
    if more_btn:
        try:
            btn_text = await more_btn.apply("(el) => el.textContent.trim()")
        except Exception:
            btn_text = ""
        if "更多选项" in (btn_text or ""):
            log.info("[3] 点击全局'更多选项'展开")
            await human_click(page, more_btn, "global btn-more")
            await page
            await asyncio.sleep(1.5)
        else:
            log.info("[3] 筛选区已展开(按钮=%s)，跳过全局点击", btn_text)

    # 2. 在所有 dl.hide.hasmore 中找到 dt[title*="面积"] 的那个
    try:
        containers = await page.select_all("dl.hide.hasmore", timeout=3)
    except Exception:
        containers = []

    area_container = None
    for c in containers:
        try:
            tit = await c.apply(
                "(el) => { const t = el.querySelector('dt'); return t ? t.title || t.textContent.trim() : ''; }"
            )
        except Exception:
            tit = ""
        if "面积" in tit:
            area_container = c
            break

    if area_container is None:
        raise RuntimeError("未找到面积筛选区（含 dt[title*=面积] 的 dl.hide.hasmore）")

    # 3. 点击面积区内的 btn-showmore 展开
    try:
        btns = await area_container.query_selector_all("span.btn-showmore")
    except Exception:
        btns = []
    if btns:
        log.info("[3] 点击面积区的 btn-showmore 展开")
        await human_click(page, btns[0], "btn-showmore")
        await page
        await asyncio.sleep(1.5)

    # 4. 找面积档位链接（注意：搜索结果页 href 格式 /ershoufang/a3rs绿景虹湾/）
    segments = [
        (0, 50, "a1"),
        (50, 70, "a2"),
        (70, 90, "a3"),
        (90, 110, "a4"),
        (110, 140, "a5"),
        (140, 170, "a6"),
        (170, 200, "a7"),
        (200, 99999, "a8"),
    ]
    target_codes = [code for low, high, code in segments if area_min < high and area_max > low]
    if not target_codes:
        raise RuntimeError(f"面积区间无可用档位: {area_min}-{area_max}")

    segment_code = target_codes[0]
    log.info("[3] 目标面积档位: %s", segment_code)

    area_link = None
    try:
        links = await area_container.query_selector_all("a")
    except Exception:
        links = []
    for lnk in links:
        try:
            href = await lnk.apply("(el) => el.getAttribute('href')")
        except Exception:
            href = ""
        if segment_code in (href or ""):
            area_link = lnk
            break

    if not area_link:
        raise RuntimeError(f"未找到面积档位: {segment_code}")

    if not await human_click(page, area_link, f"area segment {segment_code}"):
        raise RuntimeError(f"未能成功点击面积档位: {segment_code}")

    await page
    await asyncio.sleep(3)
    return True, segment_code


# ============================================================
# 第1步：打开首页 + 探测拦截
# ============================================================

def print_summary(
    *,
    open_file: Optional[Path],
    search_after_file: Optional[Path],
    area_file: Optional[Path],
    error_file: Optional[Path],
    open_url: str,
    open_title: Optional[str],
    open_blocked: bool,
    open_block_reason: Optional[str],
    search_blocked: bool,
    search_block_reason: Optional[str],
    area_confirmed: bool,
    segment_code: str,
    area_url: str,
    area_prices_count: int,
    body_len: Optional[int],
    conclusion: str,
):
    print()
    print("=" * 60)
    print("链家测试完成")
    print(f"打开首页 HTML: {open_file}")
    print(f"搜索后 HTML: {search_after_file}")
    print(f"面积筛选后 HTML: {area_file}")
    print(f"异常现场 HTML: {error_file}")
    print(f"首次打开 URL: {open_url}")
    print(f"首次打开标题: {open_title}")
    print(f"首次是否被拦: {open_blocked}")
    print(f"首次拦截原因: {open_block_reason}")
    print(f"搜索后是否被拦: {search_blocked}")
    print(f"搜索后拦截原因: {search_block_reason}")
    print(f"面积筛选点击: {area_confirmed}")
    print(f"面积档位: {segment_code}")
    print(f"面积筛选后 URL: {area_url}")
    print(f"面积筛选后在售数量: {area_prices_count}")
    print(f"正文长度: {body_len}")
    print(f"结论: {conclusion}")
    print("=" * 60)
    print()


async def main(manual_login: bool = False, debug: bool = False):
    if debug:
        set_debug_mode(True)

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    page = None
    open_file = None
    open_url = ""
    open_title: Optional[str] = None
    open_blocked = False
    open_block_reason: Optional[str] = None
    body_len: Optional[int] = None
    search_after_file = None
    search_blocked = False
    search_block_reason: Optional[str] = None
    area_file = None
    area_confirmed = False
    segment_code = ""
    area_url = ""
    area_prices_count = 0

    try:
        # ---- 第1步：打开首页 ----
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        open_file = await dump_html(page, "lj_opened")

        open_url = page.target.url or ""
        open_html = await page.get_content()
        if is_captcha_url(open_url) or is_captcha_html(open_html):
            open_blocked = True
            open_block_reason = "命中验证码拦截"
        elif is_login_html(open_html):
            open_blocked = True
            open_block_reason = "命中登录页"
        log.info("[1] 首次打开 URL: %s, 是否被拦: %s", open_url, open_blocked)

        try:
            open_title = await page.evaluate("document.title", return_by_value=True)
            body_len = await page.evaluate("document.body.innerText.length", return_by_value=True)
        except Exception as exc:
            log.warning("读取页面信息失败: %s", exc)

        # ---- 人工处理后重新打开 ----
        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(START_URL)
            await page
            await asyncio.sleep(3)
            open_file = await dump_html(page, "lj_reopened")

        # ---- 第2步：搜索小区 ----
        if not open_blocked or manual_login:
            log.info("[2] 搜索小区: %s", COMMUNITY_NAME)
            try:
                result_html = await _search_community(page, COMMUNITY_NAME)
                search_after_file = await dump_html(page, "lj_search_after")
                result_url = page.target.url or ""

                # 搜索成功判定：页面含 sellListContent 和小区名
                search_success = (
                    "sellListContent" in result_html
                    and community_name in result_html
                )
                if not search_success:
                    search_blocked = True
                    search_block_reason = "搜索未成功（页面无数据）"

                log.info("[2] 搜索后 URL: %s, 搜索成功: %s", result_url, search_success)
            except Exception as exc:
                log.warning("[2] 搜索异常: %s", exc)
                search_blocked = True
                search_block_reason = f"搜索异常: {exc}"

        # ---- 第3步：面积筛选 ----
        if not search_blocked:
            log.info("[3] 填写面积筛选: %d-%d", AREA_MIN, AREA_MAX)
            area_confirmed, segment_code = await apply_area_filter(page, AREA_MIN, AREA_MAX)
            area_file = await dump_html(page, "lj_after_area")
            area_url = page.target.url or ""
            area_html = await page.get_content()
            area_prices_count = area_html.count("元/㎡")
            log.info("[3] 面积档位: %s, 筛选后 URL: %s, 在售数量: %d", segment_code, area_url, area_prices_count)

        # 判定结论
        if open_blocked and not manual_login:
            conclusion = f"首次被拦：{open_block_reason}，建议加 --manual-login。"
        elif search_blocked:
            conclusion = f"搜索后被拦：{search_block_reason}。"
        elif area_confirmed:
            conclusion = f"搜索+面积筛选成功：{COMMUNITY_NAME} {AREA_MIN}-{AREA_MAX}㎡ 档位 {segment_code}，在售 {area_prices_count} 条。"
        else:
            conclusion = "面积筛选未能成功，需查看 HTML。"

        print_summary(
            open_file=open_file,
            search_after_file=search_after_file,
            area_file=area_file,
            error_file=None,
            open_url=open_url,
            open_title=open_title,
            open_blocked=open_blocked,
            open_block_reason=open_block_reason,
            search_blocked=search_blocked,
            search_block_reason=search_block_reason,
            area_confirmed=area_confirmed,
            segment_code=segment_code,
            area_url=area_url,
            area_prices_count=area_prices_count,
            body_len=body_len,
            conclusion=conclusion,
        )

        await wait_for_manual_close()
    except Exception:
        error_file = None
        if page is not None:
            error_file = await dump_html(page, "lj_error")
        log.exception("测试异常中断")
        raise
    finally:
        browser.stop()


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="首次打开若被拦截，先人工过验证码 / 登录，回车后重新打开再探测。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML 到 debug 目录。",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(main(manual_login=args.manual_login, debug=args.debug))


if __name__ == "__main__":
    cli()
