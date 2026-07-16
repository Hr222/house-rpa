# -*- coding: utf-8 -*-
"""安居客 MVP 测试脚本。

完整业务链路：打开首页 → 人工登录 → 搜索小区 → 面积筛选 →
在售解析 → 挂牌均价顶替成交 → 算法决策。

用法：
  python -m app.scripts.ajk_mvp_test --manual-login --debug
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import nodriver as uc

from app.core import config
from app.utils.debug_utils import dump_html as shared_dump_html
from app.utils.debug_utils import set_debug_mode
from app.core.models import ListingSnapshot
from app.core.algorithm import decide
from app.utils.mvp_result import print_mvp_result
from app.utils.logging_utils import setup_logging

setup_logging()
log = logging.getLogger("ajk-mvp-test")

# 安居客深圳二手房首页
START_URL = "https://shenzhen.anjuke.com/sale/"

# 固定测试场景：与贝壳脚本一致
COMMUNITY_NAME = "绿景虹湾"
AREA = 80.0


# ============================================================
# 通用：HTML 导出 / 拦截判定 / 人工等待
# ============================================================

async def dump_html(page, name: str) -> Optional[Path]:
    return await shared_dump_html(page, name, logger=log)


def is_captcha_url(url: str) -> bool:
    """安居客 58 系验证码拦截页 URL 特征。"""
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
# 第2步：搜索框定位 / 提交 / 真人点击
# ============================================================

async def is_interactable(element) -> bool:
    try:
        pos = await element.get_position()
        return bool(pos and pos.width > 0 and pos.height > 0)
    except Exception:
        return False


async def get_search_input(page):
    """定位安居客搜索框。

    安居客首页搜索框占位符为"请输入小区名称、地址"，
    先按多候选选择器找，找不到再按占位符文本兜底。
    """
    selectors = [
        "input#search-input",
        "input#searchInput",
        "input.search-input",
        "input[name='keyword']",
        "input[placeholder*='小区']",
        "input[placeholder*='地址']",
        "input[placeholder*='搜索']",
        "#sale-content input",
    ]
    for selector in selectors:
        try:
            elements = await page.select_all(selector, timeout=1.5)
        except Exception:
            continue
        for element in elements:
            if await is_interactable(element):
                return selector, element
        if elements:
            return selector, elements[0]
    return None, None


async def get_search_submit(page):
    """定位安居客搜索提交按钮。"""
    selectors = [
        "button.search-btn",
        "a.search-btn",
        "button[type='submit']",
        "input[type='submit']",
        ".btn-search",
        "button.btn",
    ]
    for selector in selectors:
        try:
            elements = await page.select_all(selector, timeout=1.5)
        except Exception:
            continue
        for element in elements:
            if await is_interactable(element):
                return selector, element
        if elements:
            return selector, elements[0]
    return None, None


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
# 第3步：面积自定义输入（安居客是填值+点确定，不是预设档位）
# ============================================================

async def fill_area_inputs(page, area_min, area_max):
    """定位面积筛选区的自定义输入框并填值。

    安居客结果页里有两处 input.input：一处在价格筛选区（unit=万），
    一处在面积筛选区（unit=㎡）。靠父级 li 内的 unit 文本是"㎡"区分。
    填值后"确定"按钮会显示，需点击提交。

    注意：Element 用 query_selector_all（不是 select_all，那是 Tab 的方法）。
    """
    # 1. 遍历所有 line-item-input，靠 unit 文本是"㎡"定位面积区
    line_items = await page.select_all("li.line-item-input")
    log.info("[3] line-item-input 数量: %d", len(line_items))

    area_li = None
    for li in line_items:
        unit_text = await li.apply(
            "(el) => { const u = el.querySelector('.unit'); return u ? u.textContent.trim() : ''; }"
        )
        log.info("[3]   li unit 文本: %r", unit_text)
        if unit_text == "㎡":
            area_li = li
            break

    if area_li is None:
        raise RuntimeError("未找到面积筛选区（unit=㎡ 的 line-item-input）")

    # 2. 取该 li 下两个 input
    inputs = await area_li.query_selector_all("input.input")
    log.info("[3] 面积区 input 数量: %d", len(inputs))
    if len(inputs) < 2:
        raise RuntimeError(f"面积区 input 不足 2 个，实际 {len(inputs)} 个")
    min_el, max_el = inputs[0], inputs[1]

    # 3. 填下限
    await human_click(page, min_el, "area min input")
    try:
        await min_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await min_el.send_keys(str(int(area_min)))
    await page
    await asyncio.sleep(0.5)
    log.info("[3] 填入下限: %s", area_min)

    # 4. 填上限
    await human_click(page, max_el, "area max input")
    try:
        await max_el.clear_input()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    await max_el.send_keys(str(int(area_max)))
    await page
    await asyncio.sleep(0.8)
    log.info("[3] 填入上限: %s", area_max)

    # 5. 点"确定"提交
    confirms = await area_li.query_selector_all(".confirm")
    log.info("[3] 确定按钮数量: %d", len(confirms))
    confirm_clicked = False
    if confirms:
        confirm_clicked = await human_click(page, confirms[0], "area confirm")
    if not confirm_clicked:
        # 兜底回车
        try:
            await max_el.send_keys("\r")
            await page
            confirm_clicked = True
            log.info("[3] 用回车兜底提交")
        except Exception:
            pass

    await page
    await asyncio.sleep(3)
    return confirm_clicked


# ============================================================
# 第3.5步：滚动到底触发懒加载（安居客可能是瀑布流），拿全量房源
# ============================================================

async def scroll_to_bottom(page, max_rounds: int = 20, wait: float = 1.8) -> int:
    """循环滚动到页面底部，触发懒加载，返回实际滚动轮数。

    安居客 Vue 瀑布流：滚动到底会加载下一批房源。
    判定到底：连续两次滚动后 body 高度不变。
    """
    last_height = 0
    rounds = 0
    for i in range(max_rounds):
        try:
            current = await page.evaluate("document.body.scrollHeight", return_by_value=True)
        except Exception:
            current = last_height
        if current == last_height and i > 0:
            log.info("[3.5] 已到底（高度不变），共滚动 %d 轮", rounds)
            break
        last_height = current
        rounds += 1
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await page
        except Exception:
            pass
        await asyncio.sleep(wait)

    return rounds


# ============================================================
# 第4步：解析主结果区在售单价（截断到"推荐以下房源"之前）
# ============================================================

def parse_main_listing_prices(html: str) -> list:
    """提取主结果区在售单价（兼容旧调用）。"""
    return [s.unit_price for s in parse_listing_snapshots(html) if s.unit_price]


def _extract_first(pattern, text, cast=float):
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return cast(m.group(1).replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_listing_snapshots(html: str) -> list:
    """提取主结果区房源快照。

    安居客结果页结构：主结果区与推荐区是两个并列的 <section class="list">，
    中间靠 <h3 class="list-guess-title">分隔。只取边界标志之前的部分。

    单条房源字段：
      - 户型: property-content-info-attribute（如 3室2厅2卫）
      - 面积: property-content-info-text 里的 XX.XX㎡
      - 小区名: property-content-info-comm-name
      - 总价: property-price-total-num
      - 单价: property-price-average
    """
    cut = html.find("list-guess-title")
    main_html = html[:cut] if cut > 0 else html

    snapshots = []
    for block in re.finditer(
        r'<div[^>]*class="property"[^>]*>(.*?)(?=<div[^>]*class="property"|$)',
        main_html,
        re.S,
    ):
        chunk = block.group(1)

        # 户型: <p class="...attribute"><span>3</span>室<span>2</span>厅<span>2</span>卫
        layout = None
        attr_m = re.search(
            r'property-content-info-attribute[^>]*>(.*?)</p>', chunk, re.S
        )
        if attr_m:
            nums = re.findall(r'<span[^>]*>(\d+)</span>', attr_m.group(1))
            labels = re.findall(r'(室|厅|卫)', attr_m.group(1))
            if len(nums) >= 2:
                layout = f"{nums[0]}室{nums[1]}厅"

        # 面积: property-content-info-text 里的 XX.XX㎡
        area = _extract_first(r'([\d.]+)\s*㎡', chunk)

        # 小区名
        name_m = re.search(
            r'property-content-info-comm-name[^>]*>([^<]+)<', chunk
        )
        community_name = name_m.group(1).strip() if name_m else None

        # 总价(万)
        total_price = _extract_first(
            r'property-price-total-num[^>]*>\s*([\d,]+)', chunk
        )

        # 单价
        unit_price = _extract_first(
            r'property-price-average[^>]*>\s*([\d,]+)\s*元', chunk
        )

        if unit_price is None and total_price is None:
            continue

        snapshots.append(
            ListingSnapshot(
                house_id="",
                community_name=community_name,
                area=area,
                layout=layout,
                unit_price=unit_price,
                total_price=total_price,
            )
        )
    return snapshots


def print_listing_snapshots(snapshots: list):
    """对齐 batch_mvp_test.print_platform_details 的打印格式。"""
    if not snapshots:
        print("安居客: 未抓到房源摘要")
        return
    for item in snapshots:
        print(
            "安居客: "
            f"{{小区名称: {item.community_name or ''}, 面积: {item.area or ''}平米, "
            f"几房几厅: {item.layout or ''}, 售价: {item.unit_price or ''}元/平, "
            f"总价: {item.total_price or ''}万}}"
        )


def parse_community_avg_price(html: str):
    """从结果页顶部社区卡片提取挂牌均价。

    安居客结果页顶部社区信息卡：
      <div class="community-info-detail-price">
        <p class="community-info-detail-price-money"><em>84307</em>元/㎡</p>
      </div>

    注意：安居客无成交记录，业务上把挂牌均价当作 deal_prices 的替代，
    让 decide() 正常按"在售均价 vs 成交均价"对比出最终价。
    """
    m = re.search(
        r'community-info-detail-price-money[^>]*>\s*<em[^>]*>\s*([\d,]+)\s*</em>\s*元\s*/?\s*㎡',
        html,
    )
    return float(m.group(1).replace(",", "")) if m else None


# ============================================================
# 结果汇报
# ============================================================

def print_summary(
    *,
    open_file: Optional[Path],
    before_file: Optional[Path],
    after_file: Optional[Path],
    area_file: Optional[Path],
    open_url: str,
    open_blocked: bool,
    open_block_reason: Optional[str],
    result_url: str,
    result_blocked: bool,
    result_block_reason: Optional[str],
    area_confirmed: bool,
    area_url: str,
    listing_snapshots: list,
    listing_avg: Optional[float],
    listing_price: Optional[float],
    final_price: Optional[float],
    branch: str,
    conclusion: str,
):
    print_mvp_result(
        platform="安居客",
        community_name=COMMUNITY_NAME,
        area=AREA,
        trace={
            "home_blocked": open_blocked,
            "search_url": result_url,
            "area_ok": area_confirmed,
            "area_url": area_url,
            "area_pages": 0,
        },
        listings={
            "count": len(listing_snapshots),
            "avg": listing_avg,
            "snapshots": listing_snapshots,
        },
        deals={
            "count": 0,
            "avg": listing_price,
            "records": [],
            "substitute": f"挂牌均价顶替 {listing_price}元/㎡",
        },
        result={
            "quote_avg": listing_avg or 0,
            "deal_avg": listing_price,
            "final_price": final_price or 0,
            "branch": branch,
        },
        elapsed=0,
    )


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
    before_file = None
    after_file = None
    area_file = None
    open_url = ""
    open_blocked = False
    open_block_reason: Optional[str] = None
    search_input_selector = None
    submit_selector = None
    result_url = ""
    result_title: Optional[str] = None
    result_blocked = False
    result_block_reason: Optional[str] = None
    body_len: Optional[int] = None
    prices_count = 0
    area_confirmed = False
    area_url = ""
    area_prices_count = 0
    main_listing_prices: list = []
    listing_avg = None
    listing_price = None           # 挂牌均价（社区卡片）
    deal_avg = None                # 安居客：挂牌均价顶替成交均价
    final_price = None
    branch = ""

    try:
        # ---- 第1步：打开首页 ----
        page = await browser.get(START_URL)
        await page
        await asyncio.sleep(3)
        open_file = await dump_html(page, "ajk_opened")

        open_url = page.target.url or ""
        open_html = await page.get_content()
        if is_captcha_url(open_url) or is_captcha_html(open_html):
            open_blocked = True
            open_block_reason = "命中验证码拦截"
        elif is_login_html(open_html):
            open_blocked = True
            open_block_reason = "命中登录页"
        log.info("[1] 首次打开 URL: %s, 是否被拦: %s", open_url, open_blocked)

        # ---- 人工处理后重新打开 ----
        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(START_URL)
            await page
            await asyncio.sleep(3)

        before_file = await dump_html(page, "ajk_search_before")

        # ---- 第2步：搜索绿景虹湾 ----
        search_input_selector, inp = await get_search_input(page)
        if inp is None:
            raise RuntimeError("未找到安居客搜索框")
        log.info("[2] 搜索框命中: %s", search_input_selector)

        clicked = await human_click(page, inp, "search input")
        if not clicked:
            raise RuntimeError("搜索框未能成功点击")

        try:
            await inp.clear_input()
        except Exception:
            await inp.send_keys("\uE009a")
            await inp.send_keys("\uE017")
            await page
        await asyncio.sleep(0.5)
        await inp.send_keys(COMMUNITY_NAME)
        await page
        await asyncio.sleep(1.0)

        # 优先回车提交；失败再找提交按钮点击
        submitted = False
        try:
            await inp.send_keys("\r")
            await page
            await asyncio.sleep(3)
            submitted = True
            submit_selector = "Enter key"
        except Exception:
            submitted = False

        if not submitted:
            submit_selector, submit_btn = await get_search_submit(page)
            if submit_btn and await human_click(page, submit_btn, "search submit"):
                await page
                await asyncio.sleep(3)
                submitted = True

        if not submitted:
            raise RuntimeError("未能提交搜索")

        after_file = await dump_html(page, "ajk_search_after")

        # 探测结果页
        result_url = page.target.url or ""
        result_html = await page.get_content()
        try:
            result_title = await page.evaluate("document.title")
            body_len = await page.evaluate("document.body.innerText.length")
        except Exception as exc:
            log.warning("读取结果页信息失败: %s", exc)

        if is_captcha_url(result_url) or is_captcha_html(result_html):
            result_blocked = True
            result_block_reason = "命中验证码拦截"
        elif is_login_html(result_html):
            result_blocked = True
            result_block_reason = "命中登录页"

        # 粗略统计结果页疑似在售房源数量（暂不精确解析，后续步骤再细化）
        if not result_blocked:
            prices_count = result_html.count("元/㎡") + result_html.count("元/平米")

        # ---- 第3步：面积自定义筛选 70-90 ----
        if not result_blocked:
            log.info("[3] 填写面积筛选: %.1f", AREA)
            area_confirmed = await fill_area_inputs(page, AREA, AREA)
            area_file = await dump_html(page, "ajk_after_area")

            area_url = page.target.url or ""
            area_html = await page.get_content()
            if is_captcha_url(area_url) or is_captcha_html(area_html):
                result_blocked = True
                result_block_reason = "面积筛选后命中验证码拦截"
            area_prices_count = area_html.count("元/㎡") + area_html.count("元/平米")
            log.info("[3] 面积筛选后 URL: %s, 在售数量: %d", area_url, area_prices_count)

            # ---- 第3.5步：滚动到底触发懒加载，拿全量房源 ----
            prices_before_scroll = len(parse_main_listing_prices(area_html))
            scroll_rounds = await scroll_to_bottom(page)
            area_html = await page.get_content()
            log.info("[3.5] 滚动 %d 轮完成", scroll_rounds)

            # ---- 第4步：解析主结果区房源快照（截断到"推荐以下房源"之前）----
            listing_snapshots = parse_listing_snapshots(area_html)
            main_listing_prices = [s.unit_price for s in listing_snapshots if s.unit_price]
            log.info(
                "[4] 主结果区房源: %d 条（滚动前 %d 条）, 单价前5条: %s",
                len(listing_snapshots),
                prices_before_scroll,
                main_listing_prices[:5],
            )
            print()
            print("-" * 60)
            print_listing_snapshots(listing_snapshots)
            print("-" * 60)

            # ---- 算最终价：在售均价 vs 挂牌均价（顶替成交均价）----
            listing_avg = sum(main_listing_prices) / len(main_listing_prices)
            listing_price = parse_community_avg_price(area_html)
            # 安居客无成交记录，把挂牌均价作为 deal_prices 唯一元素
            deal_avg = listing_price
            decision = decide(
                quote_avg=listing_avg,
                deal_avg=deal_avg,
                diff_threshold=config.DEAL_DIFF_THRESHOLD,
                no_deal_discount=config.get_no_deal_discount(),
            )
            final_price = decision.final_price
            branch = decision.branch
            log.info(
                "[4] 在售均价=%.2f 挂牌均价(顶替成交)=%s 最终价=%.2f 分支=%s",
                listing_avg,
                deal_avg,
                final_price,
                branch,
            )

        # 判定结论
        if result_blocked:
            conclusion = f"流程被拦：{result_block_reason}，需人工处理或重试。"
        elif not area_confirmed:
            conclusion = "面积筛选未能成功提交，需查看 HTML 确认输入框与确定按钮。"
        elif main_listing_prices:
            conclusion = (
                f"采集成功：{AREA}㎡ 主结果区在售 {len(main_listing_prices)} 条，"
                f"在售均价 {listing_avg:.2f} 元/㎡，挂牌均价 {listing_price} 元/㎡（顶替成交），"
                f"最终价 {final_price} 元/㎡（{branch}）。"
            )
        elif area_prices_count > 0:
            conclusion = (
                f"面积筛选成功但主结果区未解析到单价（在售{area_prices_count}），需查看 HTML。"
            )
        elif prices_count > 0:
            conclusion = "搜索成功但面积筛选后未识别到在售，需查看筛选后 HTML。"
        else:
            conclusion = "流程已执行，但未识别到在售房源，需查看 HTML 确认 DOM 结构。"

        print_summary(
            open_file=open_file,
            before_file=before_file,
            after_file=after_file,
            area_file=area_file,
            open_url=open_url,
            open_blocked=open_blocked,
            open_block_reason=open_block_reason,
            result_url=result_url,
            result_blocked=result_blocked,
            result_block_reason=result_block_reason,
            area_confirmed=area_confirmed,
            area_url=area_url,
            listing_snapshots=listing_snapshots,
            listing_avg=listing_avg,
            listing_price=listing_price,
            final_price=final_price,
            branch=branch,
            conclusion=conclusion,
        )

        await wait_for_manual_close()
    except Exception:
        error_file = None
        if page is not None:
            error_file = await dump_html(page, "ajk_error")
        log.exception("测试异常中断")
        raise
    finally:
        browser.stop()


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="首次打开若被拦截，先人工过验证码 / 登录，回车后重新打开再继续。",
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
