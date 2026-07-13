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
# 第3步：面积筛选（链家需先点"更多及自定义"展开，再点面积档位）
# ============================================================

async def apply_area_filter(page, area_min, area_max):
    """链家面积筛选：先展开隐藏区，再点对应面积档位。

    链家 DOM：
      <span class="btn-showmore">+ 更多及自定义</span>  ← 展开按钮
      <dl class="hide hasmore">                          ← 展开后显示
        <dt title="...面积">面积</dt>
        <dd><a href="/ershoufang/a3/"><span class="name">70-90㎡</span></a></dd>

    面积档位和贝壳一致：a1=50以下 a2=50-70 a3=70-90 a4=90-110 a5=110-140 a6=140-170 a7=170以上
    """
    # 1. 点"更多及自定义"展开隐藏的筛选区
    showmore_clicked = False
    try:
        showmore = await page.select("span.btn-showmore", timeout=3)
    except Exception:
        showmore = None
    if showmore:
        showmore_clicked = await human_click(page, showmore, "btn-showmore")
    if not showmore_clicked:
        log.warning("未能点击'更多及自定义'，尝试直接找面积档位")
    await asyncio.sleep(1.5)

    # 2. 找面积档位链接（70-90㎡ = a3）
    # 面积档位映射（和贝壳一致）
    segments = [
        (0, 50, "a1"),
        (50, 70, "a2"),
        (70, 90, "a3"),
        (90, 110, "a4"),
        (110, 140, "a5"),
        (140, 170, "a6"),
        (170, 99999, "a7"),
    ]
    target_codes = [code for low, high, code in segments if area_min < high and area_max > low]
    if not target_codes:
        raise RuntimeError(f"面积区间无可用档位: {area_min}-{area_max}")

    # 点第一个匹配的档位（70-90 对应 a3）
    segment_code = target_codes[0]
    selector = f"a[href*='/{segment_code}/']"
    log.info("[3] 目标面积档位: %s, 选择器: %s", segment_code, selector)

    area_link = None
    try:
        area_link = await page.select(selector, timeout=3)
    except Exception:
        area_link = None

    # 兜底：按文本找"70-90㎡"
    if not area_link:
        area_label = f"{int(area_min)}-{int(area_max)}㎡"
        try:
            area_link = await page.find(area_label, timeout=3)
        except Exception:
            area_link = None

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
    area_file: Optional[Path],
    error_file: Optional[Path],
    open_url: str,
    open_title: Optional[str],
    open_blocked: bool,
    open_block_reason: Optional[str],
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
    print(f"面积筛选后 HTML: {area_file}")
    print(f"异常现场 HTML: {error_file}")
    print(f"首次打开 URL: {open_url}")
    print(f"首次打开标题: {open_title}")
    print(f"首次是否被拦: {open_blocked}")
    print(f"首次拦截原因: {open_block_reason}")
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

        # ---- 第3步：面积筛选（先展开"更多及自定义"，再点面积档位）----
        if not open_blocked or manual_login:
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
        elif area_confirmed:
            conclusion = f"面积筛选成功：{AREA_MIN}-{AREA_MAX}㎡ 档位 {segment_code}，在售 {area_prices_count} 条。"
        else:
            conclusion = "面积筛选未能成功，需查看 HTML。"

        print_summary(
            open_file=open_file,
            area_file=area_file,
            error_file=None,
            open_url=open_url,
            open_title=open_title,
            open_blocked=open_blocked,
            open_block_reason=open_block_reason,
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
