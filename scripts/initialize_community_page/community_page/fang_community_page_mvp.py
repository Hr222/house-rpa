# -*- coding: utf-8 -*-
"""房天下小区挂牌页 URL 初始化 MVP（批量，支持主数据别名兜底与软风控自愈）。

流程：人工登录/验证（可选）→ 逐个小区搜索 → 结构化房源确认小区后读取
小区名链接 `/house-xm{id}/` → 实际打开挂牌页再次校验并输出可记录的挂牌页 URL。

给定名未匹配到目标小区时，查询小区主数据（只读）换正式名与其余别名重试；
全部候选名失败才判定该小区失败。单个小区失败只记录并继续，不关闭浏览器；
浏览器仅在全部小区处理完并经人工确认后才退出。

软风控自愈：单次搜索超过 ``SOFT_BLOCK_SECONDS``（45s）判定触发风控，
自动关闭浏览器并重开新会话重试当前小区；连续 ``MAX_CONSECUTIVE_RESTARTS``
（2）次新会话仍触发则判定 IP 级限流，停止批次交人工。

本脚本只用于独立验证，不写入数据库，也不接入正式采集链路；运行结果通过
``--result-file`` 输出 JSON，由编排层负责落库。

用法：
  python -m scripts.initialize_community_page.community_page.fang_community_page_mvp --manual-login \
      --city "深圳" --administrative-district "龙岗区" \
      --community "翰熙典居" "远洋新干线"
"""

from __future__ import annotations

import argparse
import asyncio
from html import unescape
import json
import logging
from pathlib import Path
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import nodriver as uc

from app.community_data import resolve_communities
from app.rpa.core import config
from app.rpa.platforms.fang import parser as fang_parsers
from app.rpa.platforms.base import community_name_match, has_matching_community_snapshots
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging


setup_logging()
log = logging.getLogger(__name__)

# 软风控参数：单次“搜索+取结果”超过该秒数判定触发风控（正常 15~18s、风控停滞 60~116s）；
# 连续 N 次换新浏览器仍触发则判定 IP 级限流，停止批次交人工。
SOFT_BLOCK_SECONDS = 45.0
MAX_CONSECUTIVE_RESTARTS = 2


class SoftBlockError(RuntimeError):
    """搜索响应超过阈值，判定触发软风控。"""


async def dump_html(page, name: str) -> Optional[Path]:
    """在开启 --debug 时导出当前 HTML。"""
    return await shared_dump_html(page, name, logger=log)


def _safe_file_token(value: str) -> str:
    """把小区名转成可用于导出文件名的安全片段。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value or "").strip("_") or "unnamed"


def _blocked_reason(url: str, html: str, expected_host: str) -> Optional[str]:
    """识别 MVP 阶段已知的登录、验证码和城市漂移现场。"""
    url_lower = (url or "").lower()
    if any(marker in url_lower for marker in ("captcha", "verifycode", "antibot", "antispam")):
        return "命中验证码拦截 URL"

    if any(marker in (html or "") for marker in ("请输入验证码", "验证后继续访问", "请完成验证", "滑动验证")):
        return "命中验证码拦截页面"

    if any(marker in (html or "") for marker in ("请输入手机号", "请输入密码", "手机快捷登录", "扫码登录")):
        return "命中登录页面"

    actual_host = (urlparse(url or "").hostname or "").lower()
    if actual_host != expected_host:
        return f"城市漂移：期望域名 {expected_host}，实际 {actual_host or '空'}"
    return None


async def wait_for_manual_resolution(label: str, reason: str) -> None:
    """保留浏览器现场，等待人工完成登录或验证码。"""
    prompt = (
        f"\n{label}{reason}。请在浏览器完成处理后，"
        "回到终端按回车继续...\n"
    )
    await asyncio.to_thread(input, prompt)


async def ensure_accessible(page, *, label: str, expected_host: str) -> str:
    """保留人工处理后的当前页，直到页面可继续解析。"""
    attempt = 1
    while True:
        await page
        html = await page.get_content()
        url = page.target.url or ""
        reason = _blocked_reason(url, html, expected_host)
        if reason is None:
            return html

        log.warning("%s不可用（第 %d 次）：%s，URL=%s", label, attempt, reason, url)
        await wait_for_manual_resolution(label, reason)
        await page
        await asyncio.sleep(2)
        attempt += 1


async def is_interactable(element) -> bool:
    """避免优先命中隐藏的搜索控件。"""
    try:
        position = await element.get_position()
        return bool(position and position.width > 0 and position.height > 0)
    except Exception:
        return False


async def get_search_input(page):
    """定位房天下二手房搜索框。"""
    selectors = (
        "input#input_keyw1",
        "input[name='input_keyw1']",
        "input[placeholder*='小区']",
    )
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
    """定位房天下二手房搜索提交按钮。"""
    selectors = (
        "input#kesfqbfylb_A01_11_02",
        "input[type='submit'].btn",
        "input[type='submit'][value*='搜']",
    )
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
    """以既有 MVP 的鼠标优先、JS 回退方式点击页面元素。"""
    if element is None:
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
            await asyncio.sleep(1)
            return True
        except Exception as exc:
            last_error = exc
    log.warning("%s点击失败：%s", label, last_error)
    return False


async def search_community(page, community_name: str) -> None:
    """在城市二手房首页提交目标小区搜索。"""
    selector, search_input = await get_search_input(page)
    if search_input is None:
        raise RuntimeError("未找到房天下搜索框")
    log.info("搜索框命中：%s", selector)

    if not await human_click(page, search_input, "搜索框"):
        raise RuntimeError("搜索框点击失败")
    try:
        await search_input.clear_input()
    except Exception:
        pass
    await search_input.send_keys(community_name)
    await page
    await asyncio.sleep(1)

    submit_selector, submit_button = await get_search_submit(page)
    if submit_button is not None and await human_click(page, submit_button, "搜索按钮"):
        log.info("通过 %s 提交小区搜索", submit_selector)
        await asyncio.sleep(3)
        return

    try:
        await search_input.send_keys("\r")
        await page
        await asyncio.sleep(3)
        log.info("通过回车提交小区搜索")
        return
    except Exception as exc:
        raise RuntimeError("未能提交房天下小区搜索") from exc


def find_matching_listing_url(
    result_html: str,
    community_name: str,
    result_url: str,
) -> Optional[str]:
    """从搜索结果 HTML 读取名称匹配小区的 `/house-xm{id}/` 地址。"""
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        result_html or "",
        re.S | re.I,
    ):
        attrs = match.group("attrs")
        href_match = re.search(
            r"\bhref=[\"'](?P<href>[^\"']*/house-xm[^\"']*)[\"']",
            attrs,
            re.I,
        )
        if href_match is None:
            continue
        title_match = re.search(r"\btitle=[\"'](?P<title>[^\"']*)[\"']", attrs, re.I)
        title = unescape(title_match.group("title")) if title_match else ""
        text = unescape(re.sub(r"<[^>]+>", "", match.group("body"))).strip()
        if community_name_match(community_name, title) or community_name_match(
            community_name,
            text,
        ):
            listing_url = urljoin(result_url, href_match.group("href"))
            if "/house-xm" in urlparse(listing_url).path:
                return listing_url
    return None


def build_candidate_names(city: str, administrative_district: str, community_name: str) -> list[str]:
    """查询小区主数据构造搜索候选名：给定名优先，正式名与其余别名兜底。"""
    names = [community_name]
    try:
        records = resolve_communities(city, administrative_district, community_name)
    except Exception as exc:
        log.warning("查询小区主数据失败，仅使用给定名搜索：%s（%s）", community_name, exc)
        return names
    if len(records) != 1:
        log.info("主数据未唯一命中（%d 条），仅使用给定名搜索：%s", len(records), community_name)
        return names
    for candidate in [records[0].name, *(records[0].aliases or ())]:
        normalized = re.sub(r"\s+", "", str(candidate or ""))
        if normalized and normalized not in names:
            names.append(normalized)
    log.info("小区[%s]候选搜索名：%s", community_name, names)
    return names


async def search_and_extract_listing(
    page,
    *,
    start_url: str,
    expected_host: str,
    candidate_name: str,
) -> Optional[str]:
    """按单个候选名搜索并提取目标小区挂牌页链接；未命中返回 None。"""
    await page.get(start_url)
    await page
    await asyncio.sleep(2)
    await ensure_accessible(page, label=f"首页[{candidate_name}]", expected_host=expected_host)

    search_started = time.perf_counter()
    await search_community(page, candidate_name)
    result_html = await ensure_accessible(
        page,
        label=f"搜索结果页[{candidate_name}]",
        expected_host=expected_host,
    )
    elapsed = time.perf_counter() - search_started
    if elapsed >= SOFT_BLOCK_SECONDS:
        raise SoftBlockError(
            f"搜索名[{candidate_name}]耗时 {elapsed:.0f}s ≥ {SOFT_BLOCK_SECONDS:.0f}s，判定触发软风控"
        )
    await dump_html(page, f"fang_community_page_search_{_safe_file_token(candidate_name)}")

    snapshots = fang_parsers.parse_listing_snapshots(result_html)
    if not has_matching_community_snapshots(snapshots, candidate_name):
        examples = sorted({item.community_name for item in snapshots if item.community_name})[:5]
        log.info(
            "搜索名[%s]结构化房源未匹配目标小区，页面小区示例：%s",
            candidate_name,
            examples,
        )
        return None

    result_url = page.target.url or start_url
    listing_page_url = find_matching_listing_url(result_html, candidate_name, result_url)
    if listing_page_url is None:
        log.info("搜索名[%s]未找到目标小区的 /house-xm 挂牌页链接", candidate_name)
        return None
    return listing_page_url


async def collect_community(
    page,
    *,
    start_url: str,
    expected_host: str,
    city: str,
    administrative_district: str,
    community_name: str,
) -> dict:
    """处理单个小区：候选名兜底搜索 → 打开挂牌页复核 → 返回结果摘要。"""
    names = build_candidate_names(city, administrative_district, community_name)
    listing_page_url: Optional[str] = None
    matched_name = ""
    for name in names:
        listing_page_url = await search_and_extract_listing(
            page,
            start_url=start_url,
            expected_host=expected_host,
            candidate_name=name,
        )
        if listing_page_url is not None:
            matched_name = name
            break
    if listing_page_url is None:
        raise RuntimeError(f"候选名 {names} 均未匹配到目标小区挂牌页链接，拒绝自动选择")

    await page.get(listing_page_url)
    await page
    await asyncio.sleep(2)
    listing_html = await ensure_accessible(
        page,
        label="小区挂牌页",
        expected_host=expected_host,
    )
    await dump_html(page, f"fang_community_page_listing_{_safe_file_token(community_name)}")
    listing_snapshots = fang_parsers.parse_listing_snapshots(listing_html)
    if not has_matching_community_snapshots(listing_snapshots, matched_name):
        raise RuntimeError(f"挂牌页未匹配目标小区：{matched_name}")

    actual_listing_url = page.target.url or listing_page_url
    if "/house-xm" not in urlparse(actual_listing_url).path:
        raise RuntimeError(f"挂牌页跳转到非小区 URL：{actual_listing_url}")

    return {
        "city": city,
        "administrative_district": administrative_district,
        "community_name": community_name,
        "matched_search_name": matched_name,
        "listing_page_url": actual_listing_url,
        "structured_listing_count": len(listing_snapshots),
    }


async def wait_for_manual_close() -> None:
    """在结束前保留浏览器，方便核验实际页面。"""
    await asyncio.to_thread(
        input, "\n全部小区已处理完成。你确认无误后按回车结束脚本（浏览器将关闭）...\n"
    )


def build_deal_page_url(listing_page_url: str) -> str:
    """由已核对的挂牌页 URL 推导成交页 URL。

    房天下二手房小区（house-xm{id}）与楼盘（/loupan/{id}/）同 ID
    （2026-09-02 人工核对），路径直换：/house-xm{id}/ → /loupan/{id}/chengjiao/。
    """
    parsed = urlparse(listing_page_url)
    match = re.search(r"/house-xm(\d+)", parsed.path)
    if match is None:
        raise RuntimeError(f"挂牌页 URL 不含 house-xm ID，无法推导成交页：{listing_page_url}")
    return f"{parsed.scheme}://{parsed.hostname}/loupan/{match.group(1)}/chengjiao/"


async def collect_deal_page_url(page, *, listing_page_url: str, expected_host: str, community_name: str) -> str:
    """由挂牌页 URL 同 ID 推导成交页并实打开复核，返回实际成交页地址。

    推导只做路径直换；打开后核对最终 URL 含同一 loupan ID 且含
    /chengjiao/（排除全市成交页），不校验成交条数（零成交小区合法为空）。
    """
    deal_page_url = build_deal_page_url(listing_page_url)
    loupan_id = re.search(r"/house-xm(\d+)", urlparse(listing_page_url).path).group(1)

    await page.get(deal_page_url)
    await page
    await asyncio.sleep(2)
    await ensure_accessible(page, label="小区成交页", expected_host=expected_host)
    await dump_html(page, f"fang_community_page_deal_{_safe_file_token(community_name)}")
    actual_url = page.target.url or deal_page_url
    actual_path = urlparse(actual_url).path
    if f"/loupan/{loupan_id}/" not in actual_path or "/chengjiao/" not in actual_path:
        raise RuntimeError(f"成交页打开后非目标小区成交 URL：{actual_url}")
    return actual_url


async def main(
    city: str,
    administrative_district: str,
    community_names: list[str],
    manual_login: bool,
    debug: bool,
    result_file: Optional[str] = None,
    manual_close: bool = True,
    include_deal: bool = False,
) -> list[dict]:
    """批量执行房天下小区挂牌页 URL 初始化，单小区失败不中断。

    返回逐小区结果摘要（含 success 标记）；manual_close=False 时跳过结束前的
    人工确认回车；include_deal=True 时为每个成功小区追加初始化成交页 URL
    （/loupan/{id}/chengjiao/，由挂牌页 URL 同 ID 推导并实打开复核），
    供统一初始化入口复用。
    """
    if debug:
        set_debug_mode(True)

    start_url = get_start_url("fang", city)
    expected_host = (urlparse(start_url).hostname or "").lower()
    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    page = None
    try:
        page = await browser.get(start_url)
        await page
        await asyncio.sleep(3)

        if manual_login:
            await wait_for_manual_resolution("首页", "需要人工确认登录状态")
            await page.get(start_url)
            await page
            await asyncio.sleep(2)
        await ensure_accessible(
            page,
            label="首页",
            expected_host=expected_host,
        )
        await dump_html(page, "fang_community_page_home")

        summaries: list[dict] = []
        consecutive_restarts = 0
        index = 0
        while index < len(community_names):
            community_name = community_names[index]
            try:
                summary = await collect_community(
                    page,
                    start_url=start_url,
                    expected_host=expected_host,
                    city=city,
                    administrative_district=administrative_district,
                    community_name=community_name,
                )
                consecutive_restarts = 0
                index += 1
                summary["success"] = True
                if include_deal:
                    try:
                        summary["deal_page_url"] = await collect_deal_page_url(
                            page,
                            listing_page_url=summary["listing_page_url"],
                            expected_host=expected_host,
                            community_name=community_name,
                        )
                        print(f"deal_page_url：{summary['deal_page_url']}")
                    except Exception as deal_exc:
                        log.warning(
                            "小区[%s]成交页初始化失败（不影响挂牌结果）：%s", community_name, deal_exc
                        )
                        summary["deal_error"] = str(deal_exc)
                summaries.append(summary)
                print(f"\n[成功] {community_name}")
                print(f"城市：{summary['city']}")
                print(f"行政区：{summary['administrative_district']}")
                print(f"命中搜索名：{summary['matched_search_name']}")
                print(f"listing_page_url：{summary['listing_page_url']}")
                print(f"挂牌页结构化房源：{summary['structured_listing_count']} 条")
            except SoftBlockError as exc:
                consecutive_restarts += 1
                if consecutive_restarts > MAX_CONSECUTIVE_RESTARTS:
                    log.error(
                        "连续 %d 次新会话仍触发软风控，疑似 IP 级限流，停止批次（已保留 %d 条结果）",
                        consecutive_restarts - 1,
                        len(summaries),
                    )
                    print(f"\n[停止] 连续新会话仍触发风控，请人工介入后重跑剩余小区")
                    break
                log.warning(
                    "判定触发软风控（%s），关闭浏览器重开新会话，重试小区[%s]（第 %d 次重开）",
                    exc,
                    community_name,
                    consecutive_restarts,
                )
                browser.stop()
                browser = await uc.start(
                    headless=False,
                    browser_executable_path=config.BROWSER_PATH,
                    lang="zh-CN",
                )
                page = await browser.get(start_url)
                await page
                await asyncio.sleep(3)
                await ensure_accessible(
                    page,
                    label="重开首页",
                    expected_host=expected_host,
                )
            except Exception as exc:
                consecutive_restarts = 0
                index += 1
                log.error("小区[%s]处理失败，继续下一个：%s", community_name, exc)
                summaries.append(
                    {"community_name": community_name, "success": False, "error": str(exc)}
                )
                print(f"\n[失败] {community_name} → {exc}")

        print("\n===== 批量结果汇总 =====")
        for item in summaries:
            if item["success"]:
                print(
                    f"[成功] {item['community_name']}：命中搜索名={item['matched_search_name']}，"
                    f"listing={item['listing_page_url']}"
                )
            else:
                print(f"[失败] {item['community_name']}：{item['error']}")
        log.info(
            "批量处理完成：共 %d 个，成功 %d 个",
            len(summaries),
            sum(1 for item in summaries if item["success"]),
        )

        result_path = Path(result_file) if result_file else (
            Path("results") / "community_page" / f"fang_{time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"结果已写 {result_path}")

        if manual_close:
            await wait_for_manual_close()
    finally:
        browser.stop()

    return summaries


def cli() -> None:
    """解析命令行参数并启动 nodriver 协程。"""
    parser = argparse.ArgumentParser(
        description="房天下小区挂牌页 URL 初始化 MVP（批量，支持别名兜底）"
    )
    parser.add_argument("--city", default="深圳", help="城市名称，默认：深圳")
    parser.add_argument("--administrative-district", required=True, help="主数据行政区")
    parser.add_argument(
        "--community",
        required=True,
        nargs="+",
        help="目标小区名称（可多个），支持主数据别名，未命中时自动按正式名/别名兜底重试",
    )
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="打开首页后先暂停，供人工登录或确认验证码状态。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="导出首页、搜索结果和挂牌页 HTML 到 debug 目录。",
    )
    parser.add_argument(
        "--result-file",
        default=None,
        help="结果 JSON 输出路径；缺省写 results/community_page/fang_<时间戳>.json",
    )
    args = parser.parse_args()
    uc.loop().run_until_complete(
        main(
            city=args.city,
            administrative_district=args.administrative_district,
            community_names=args.community,
            manual_login=args.manual_login,
            debug=args.debug,
            result_file=args.result_file,
        )
    )


if __name__ == "__main__":
    cli()
