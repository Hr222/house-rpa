# -*- coding: utf-8 -*-
"""安居客小区挂牌页 URL 初始化 MVP（批量，支持主数据别名兜底）。

流程：人工登录/验证（可选）→ 逐个小区搜索 → 从搜索结果 HTML 读取小区名称
链接和 comm_id → 严格核对行政区 → 片区仅作辅助记录 → 拼接并实打开挂牌页。

给定名未匹配到目标小区时，查询小区主数据（只读）换正式名与其余别名重试；
全部候选名失败才判定该小区失败。单个小区失败只记录并继续，不关闭浏览器；
浏览器仅在全部小区处理完并经人工确认后才退出。

本脚本只用于独立验证，不写入数据库，也不接入正式采集链路。

用法：
  python -m scripts.rpa.ajk_community_page_mvp --manual-login \
      --city "深圳" --administrative-district "罗湖区" \
      --community "联城美园" "绿景虹湾"
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from html import unescape
import logging
from pathlib import Path
import re
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import nodriver as uc

from app.community_data import resolve_communities
from app.rpa.core import config
from app.rpa.parsers import ajk as ajk_parsers
from app.rpa.platforms.base import community_name_match, has_matching_community_snapshots
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging


setup_logging()
log = logging.getLogger(__name__)


@dataclass
class CommunityLinkCandidate:
    """搜索结果中一个可用于取得 comm_id 的小区链接。"""

    community_name: str
    href: str
    comm_id: str
    platform_administrative_district: str
    platform_area: str
    context: str


async def dump_html(page, name: str) -> Optional[Path]:
    """在开启 --debug 时导出当前 HTML。"""
    return await shared_dump_html(page, name, logger=log)


def _safe_file_token(value: str) -> str:
    """把小区名转成可用于导出文件名的安全片段。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value or "").strip("_") or "unnamed"


def normalize_district(value: str) -> str:
    """统一“福田”和“福田区”等行政区写法。"""
    text = re.sub(r"\s+", "", str(value or ""))
    for suffix in ("行政区", "新区", "区"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _visible_text(fragment: str) -> str:
    """将 HTML 片段压缩为用于位置字段识别的可见文本。"""
    fragment = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(fragment)).strip()


def _platform_location(context: str, expected_district: str) -> tuple[str, str]:
    """从小区卡片的地点节点提取行政区和片区。"""
    location_match = re.search(
        r"community-info-detail-des-item-long[^>]*>(?P<body>.*?)</p>",
        context,
        re.I | re.S,
    )
    if location_match:
        parts = [
            _visible_text(item)
            for item in re.findall(r"<span[^>]*>(.*?)</span>", location_match.group("body"), re.I | re.S)
        ]
        if len(parts) >= 2:
            return parts[0], parts[1]

    # 新版卡片把位置拆成 short 节点：行政区/片区/地址 三个并列 span。
    short_match = re.search(
        r"community-info-detail-des-item-short[^>]*>(?P<body>.*?)</p>",
        context,
        re.I | re.S,
    )
    if short_match:
        parts = [
            _visible_text(item)
            for item in re.findall(r"<span[^>]*>(.*?)</span>", short_match.group("body"), re.I | re.S)
        ]
        parts = [part for part in parts if part]
        if len(parts) >= 2:
            return parts[0], parts[1]

    # 兼容旧页面把位置直接渲染成“行政区-片区”的结构。
    stem = re.escape(normalize_district(expected_district))
    if stem:
        match = re.search(
            rf"(?P<administrative>{stem}(?:新区|区)?)\s*[-－—]\s*"
            r"(?P<area>[^\s<>&\-－—]{1,20})",
            context,
        )
        if match:
            return match.group("administrative"), match.group("area")

    # 只作为诊断信息，不用于放宽行政区硬校验。
    match = re.search(
        r"(?P<administrative>[\u4e00-\u9fff]{1,8}(?:新区|区)?)\s*[-－—]\s*"
        r"(?P<area>[^\s<>&\-－—]{1,20})",
        context,
    )
    if match:
        return match.group("administrative"), match.group("area")
    return "", ""


def extract_community_candidates(
    result_html: str,
    result_url: str,
    community_name: str,
    administrative_district: str,
) -> list[CommunityLinkCandidate]:
    """从搜索结果 HTML 提取名称匹配的小区链接及其位置上下文。"""
    pattern = re.compile(
        r"<a\b(?P<attrs>[^>]*?\bhref\s*=\s*[\"']"
        r"(?P<href>[^\"']*/community/view/(?P<comm_id>\d+)[^\"']*)[\"']"
        r"[^>]*)>(?P<body>.*?)</a>",
        re.I | re.S,
    )
    candidates: dict[str, CommunityLinkCandidate] = {}
    for match in pattern.finditer(result_html or ""):
        attrs = match.group("attrs")
        body = match.group("body")
        title_match = re.search(r"\btitle\s*=\s*[\"']([^\"']*)[\"']", attrs, re.I)
        title = unescape(title_match.group(1)) if title_match else ""
        name_match = re.search(r"<h3\b[^>]*>(?P<name>.*?)</h3>", body, re.I | re.S)
        captured_name = _visible_text(name_match.group("name")) if name_match else title
        # 内层“查看小区详情”链接复用同一个 href，但没有 h3，跳过它。
        if not captured_name:
            continue
        if not community_name_match(community_name, captured_name):
            continue

        context = body
        platform_district, platform_area = _platform_location(
            context,
            administrative_district,
        )
        href = urljoin(result_url, unescape(match.group("href")))
        comm_id = match.group("comm_id")
        candidates.setdefault(
            comm_id,
            CommunityLinkCandidate(
                community_name=captured_name.strip(),
                href=href,
                comm_id=comm_id,
                platform_administrative_district=platform_district,
                platform_area=platform_area,
                context=context,
            ),
        )
    return list(candidates.values())


def select_unique_candidate(
    candidates: list[CommunityLinkCandidate],
    administrative_district: str,
) -> CommunityLinkCandidate:
    """严格按行政区筛选，并要求最终只剩一个 comm_id。"""
    expected = normalize_district(administrative_district)
    matched = [
        candidate
        for candidate in candidates
        if normalize_district(candidate.platform_administrative_district) == expected
    ]
    if not matched:
        details = [
            {
                "name": item.community_name,
                "comm_id": item.comm_id,
                "行政区": item.platform_administrative_district or "未识别",
                "片区": item.platform_area or "未识别",
            }
            for item in candidates
        ]
        raise RuntimeError(
            f"没有候选同时满足小区名称和行政区 {administrative_district}：{details}"
        )
    if len(matched) > 1:
        raise RuntimeError(
            f"小区名称和行政区仍对应多个 comm_id，拒绝自动选择："
            f"{[(item.comm_id, item.platform_area) for item in matched]}"
        )
    return matched[0]


def blocked_reason(url: str, html: str, expected_host: str) -> Optional[str]:
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
        return f"城市漂移：期望 {expected_host}，实际 {actual_host or '空'}"
    return None


async def ensure_accessible(page, *, label: str, expected_host: str, manual: bool) -> str:
    """页面被拦截时等待人工处理，恢复后再返回 HTML。"""
    attempt = 1
    while True:
        await page
        html = await page.get_content()
        reason = blocked_reason(page.target.url or "", html, expected_host)
        if reason is None:
            return html
        if not manual:
            raise RuntimeError(f"{label}{reason}，请使用 --manual-login 后重试")
        log.warning("%s不可用（第 %d 次）：%s", label, attempt, reason)
        await asyncio.to_thread(
            input,
            f"\n{label}{reason}。请在浏览器处理完成后按回车继续...\n",
        )
        await page
        await asyncio.sleep(2)
        attempt += 1


async def is_interactable(element) -> bool:
    """避免命中隐藏的搜索控件。"""
    try:
        position = await element.get_position()
        return bool(position and position.width > 0 and position.height > 0)
    except Exception:
        return False


async def get_search_input(page):
    """定位安居客搜索框。"""
    selectors = (
        "input#search-input",
        "input#searchInput",
        "input.search-input",
        "input[name='keyword']",
        "input[placeholder*='小区']",
        "input[placeholder*='地址']",
        "input[placeholder*='搜索']",
        "#sale-content input",
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
    """以鼠标优先、JS 回退方式点击页面元素。"""
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
    for clicker in ("mouse", "js"):
        try:
            if clicker == "mouse":
                await element.mouse_click()
            else:
                await element.click()
            await page
            await asyncio.sleep(1)
            return True
        except Exception:
            continue
    log.warning("%s点击失败", label)
    return False


async def search_community(page, community_name: str) -> None:
    """在安居客首页搜索目标小区。"""
    selector, search_input = await get_search_input(page)
    if search_input is None:
        raise RuntimeError("未找到安居客搜索框")
    log.info("搜索框命中：%s", selector)
    if not await human_click(page, search_input, "搜索框"):
        raise RuntimeError("搜索框点击失败")
    try:
        await search_input.clear_input()
    except Exception:
        await search_input.send_keys("\ue009a")
        await search_input.send_keys("\ue017")
    await search_input.send_keys(community_name)
    await page
    await asyncio.sleep(1)
    try:
        await search_input.send_keys("\r")
        await page
        await asyncio.sleep(3)
    except Exception as exc:
        raise RuntimeError("未能提交安居客小区搜索") from exc


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


async def wait_for_manual_close() -> None:
    """在结束前保留浏览器，方便核验实际页面。"""
    await asyncio.to_thread(
        input, "\n全部小区已处理完成。你确认无误后按回车结束脚本（浏览器将关闭）...\n"
    )


async def search_and_extract_candidate(
    page,
    *,
    start_url: str,
    expected_host: str,
    administrative_district: str,
    candidate_name: str,
    manual_login: bool,
) -> Optional[CommunityLinkCandidate]:
    """按单个候选名搜索并提取唯一小区候选；未命中返回 None。"""
    await page.get(start_url)
    await page
    await asyncio.sleep(3)
    await ensure_accessible(
        page,
        label=f"首页[{candidate_name}]",
        expected_host=expected_host,
        manual=manual_login,
    )

    await search_community(page, candidate_name)
    result_url = page.target.url or start_url
    result_html = await ensure_accessible(
        page,
        label=f"搜索结果页[{candidate_name}]",
        expected_host=expected_host,
        manual=manual_login,
    )
    await dump_html(page, f"ajk_community_page_search_{_safe_file_token(candidate_name)}")

    try:
        snapshots = ajk_parsers.parse_listing_snapshots(result_html)
        if not has_matching_community_snapshots(snapshots, candidate_name):
            log.info("搜索名[%s]结构化房源未匹配目标小区", candidate_name)
            return None

        candidates = extract_community_candidates(
            result_html,
            result_url,
            candidate_name,
            administrative_district,
        )
        return select_unique_candidate(candidates, administrative_district)
    except RuntimeError as exc:
        log.info("搜索名[%s]未提取到目标小区唯一候选：%s", candidate_name, exc)
        return None


async def collect_community(
    page,
    *,
    start_url: str,
    expected_host: str,
    city: str,
    administrative_district: str,
    community_district: str,
    community_name: str,
    manual_login: bool,
) -> dict:
    """处理单个小区：候选名兜底搜索 → 拼接并实打开挂牌页复核 → 返回结果摘要。"""
    names = build_candidate_names(city, administrative_district, community_name)
    candidate: Optional[CommunityLinkCandidate] = None
    matched_name = ""
    for name in names:
        candidate = await search_and_extract_candidate(
            page,
            start_url=start_url,
            expected_host=expected_host,
            administrative_district=administrative_district,
            candidate_name=name,
            manual_login=manual_login,
        )
        if candidate is not None:
            matched_name = name
            break
    if candidate is None:
        raise RuntimeError(f"候选名 {names} 均未提取到目标小区唯一候选，拒绝自动选择")

    if community_district and candidate.platform_area:
        log.info(
            "片区仅作辅助：主数据=%s，平台=%s",
            community_district,
            candidate.platform_area,
        )
    elif community_district:
        log.info("片区仅作辅助：主数据=%s，平台未识别", community_district)

    origin = f"{urlparse(start_url).scheme}://{expected_host}"
    listing_page_url = urljoin(origin, f"/sale/?comm_id={candidate.comm_id}")
    await page.get(listing_page_url)
    await page
    await asyncio.sleep(3)
    listing_html = await ensure_accessible(
        page,
        label="小区挂牌页",
        expected_host=expected_host,
        manual=manual_login,
    )
    await dump_html(page, f"ajk_community_page_listing_{_safe_file_token(community_name)}")
    listing_snapshots = ajk_parsers.parse_listing_snapshots(listing_html)
    if not has_matching_community_snapshots(listing_snapshots, matched_name):
        raise RuntimeError(f"拼接后的挂牌页未匹配目标小区：{matched_name}")

    actual_url = page.target.url or listing_page_url
    actual_comm_id = parse_qs(urlparse(actual_url).query).get("comm_id", [""])[0]
    if actual_comm_id != candidate.comm_id:
        raise RuntimeError(
            f"挂牌页 comm_id 校验失败：期望 {candidate.comm_id}，实际 {actual_comm_id or '空'}"
        )

    return {
        "city": city,
        "administrative_district": administrative_district,
        "community_district": community_district,
        "community_name": community_name,
        "matched_search_name": matched_name,
        "platform_name": candidate.community_name,
        "platform_district": candidate.platform_administrative_district or "未识别",
        "platform_area": candidate.platform_area or "未识别",
        "comm_id": candidate.comm_id,
        "listing_page_url": actual_url,
        "structured_listing_count": len(listing_snapshots),
    }


async def main(
    city: str,
    administrative_district: str,
    community_district: str,
    community_names: list[str],
    manual_login: bool,
    debug: bool,
) -> None:
    """批量执行安居客小区挂牌页 URL 初始化，单小区失败不中断。"""
    if debug:
        set_debug_mode(True)
    start_url = get_start_url("ajk", city)
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
        await ensure_accessible(
            page,
            label="首页",
            expected_host=expected_host,
            manual=manual_login,
        )
        await dump_html(page, "ajk_community_page_home")

        summaries: list[dict] = []
        for community_name in community_names:
            try:
                summary = await collect_community(
                    page,
                    start_url=start_url,
                    expected_host=expected_host,
                    city=city,
                    administrative_district=administrative_district,
                    community_district=community_district,
                    community_name=community_name,
                    manual_login=manual_login,
                )
                summary["success"] = True
                summaries.append(summary)
                print(f"\n[成功] {community_name}")
                print(f"城市：{summary['city']}")
                print(f"行政区：{summary['administrative_district']}")
                print(f"片区（辅助）：{summary['community_district'] or '未提供'}")
                print(f"命中搜索名：{summary['matched_search_name']}")
                print(f"平台小区名称：{summary['platform_name']}")
                print(f"平台行政区：{summary['platform_district']}")
                print(f"平台片区：{summary['platform_area']}")
                print(f"comm_id：{summary['comm_id']}")
                print(f"listing_page_url：{summary['listing_page_url']}")
                print(f"挂牌页结构化房源：{summary['structured_listing_count']} 条")
            except Exception as exc:
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
                    f"comm_id={item['comm_id']}，listing={item['listing_page_url']}"
                )
            else:
                print(f"[失败] {item['community_name']}：{item['error']}")
        log.info(
            "批量处理完成：共 %d 个，成功 %d 个",
            len(summaries),
            sum(1 for item in summaries if item["success"]),
        )
        await wait_for_manual_close()
    finally:
        browser.stop()


def cli() -> None:
    """解析命令行参数并启动脚本。"""
    parser = argparse.ArgumentParser(
        description="安居客小区挂牌页 URL 初始化 MVP（批量，支持别名兜底）"
    )
    parser.add_argument("--city", default="深圳", help="城市名称")
    parser.add_argument("--administrative-district", required=True, help="主数据行政区")
    parser.add_argument("--community-district", default="", help="主数据片区，仅作辅助")
    parser.add_argument(
        "--community",
        required=True,
        nargs="+",
        help="目标小区名称（可多个），支持主数据别名，未命中时自动按正式名/别名兜底重试",
    )
    parser.add_argument("--manual-login", action="store_true", help="验证码/登录拦截时等待人工处理")
    parser.add_argument("--debug", action="store_true", help="导出首页、搜索页和挂牌页 HTML")
    args = parser.parse_args()
    uc.loop().run_until_complete(
        main(
            city=args.city,
            administrative_district=args.administrative_district,
            community_district=args.community_district,
            community_names=args.community,
            manual_login=args.manual_login,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    cli()
