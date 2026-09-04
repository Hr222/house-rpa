# -*- coding: utf-8 -*-
"""乐有家小区挂牌页 URL 初始化 MVP（批量，支持主数据别名兜底）。

流程：打开城市二手房页 → 按候选名逐个搜索 → 从结构化挂牌卡确认小区 ID →
按小区 ID 拼接挂牌列表入口，并实际复核页面仍命中目标小区。

给定名未匹配到目标小区时，查询小区主数据（只读）换正式名与其余别名重试；
全部候选名失败才判定该小区失败。单个小区失败只记录并继续，不关闭浏览器；
浏览器仅在全部小区处理完并经人工确认后才退出。

本脚本只用于独立验证，不写入数据库，也不接入正式 RPA 编排链路。

用法：
  python -m scripts.initialize_community_page.community_page.lyj_community_page_mvp --manual-login \
      --city "深圳" --administrative-district "福田区" \
      --community "绿景虹湾" "香蜜湖一号"
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
from urllib.parse import quote, urljoin, urlparse

import nodriver as uc

from app.community_data import resolve_communities
from app.rpa.core import config
from app.rpa.platforms.lyj import parser as lyj_parsers
from app.rpa.platforms.base import community_name_match, has_matching_community_snapshots
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging


setup_logging()
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommunityCandidate:
    """挂牌卡中发现的小区详情链接，仅作为初始化阶段辅助信息。"""

    community_name: str
    detail_url: str
    community_id: str


def _visible_text(fragment: str) -> str:
    """提取 HTML 片段可见文本。"""
    fragment = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(fragment)).strip()


def _expected_host(start_url: str) -> str:
    return (urlparse(start_url).hostname or "").lower()


def _safe_file_token(value: str) -> str:
    """把小区名转成可用于导出文件名的安全片段。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value or "").strip("_") or "unnamed"


def blocked_reason(url: str, html: str, expected_host: str) -> Optional[str]:
    """识别乐有家验证码、登录页和城市漂移。"""
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
    """被拦截时等待人工处理，恢复后返回页面 HTML。"""
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
        await asyncio.to_thread(input, f"\n{label}{reason}。请在浏览器处理完成后按回车继续...\n")
        await page
        await asyncio.sleep(2)
        attempt += 1


async def dump_html(page, name: str) -> Optional[Path]:
    """在开启 --debug 时导出当前页面 HTML。"""
    return await shared_dump_html(page, name, logger=log)


async def wait_for_manual_login() -> None:
    """搜索前固定等待人工完成登录/验证。"""
    await asyncio.to_thread(
        input,
        "\n请先在浏览器完成乐有家登录/验证，完成后回到终端按回车开始搜索小区...\n",
    )


def extract_community_candidates(result_html: str, result_url: str, community_name: str) -> list[CommunityCandidate]:
    """从真实挂牌卡提取目标小区详情链接，避免把搜索词当作命中依据。"""
    candidates: dict[str, CommunityCandidate] = {}
    for block in re.finditer(r'<li\b[^>]*class="[^" ]*item[^" ]*[^>]*>(?P<body>.*?)</li>', result_html or "", re.I | re.S):
        body = block.group("body")
        link_match = re.search(
            r'<a\b[^>]*href=["\'](?P<href>[^"\']*/xq/detail/(?P<community_id>\d+)[^"\']*)["\'][^>]*>(?P<name>.*?)</a>',
            body,
            re.I | re.S,
        )
        if not link_match:
            continue
        captured_name = _visible_text(link_match.group("name"))
        if not community_name_match(community_name, captured_name):
            continue
        detail_url = urljoin(result_url, unescape(link_match.group("href")))
        candidates.setdefault(
            link_match.group("community_id"),
            CommunityCandidate(
                community_name=captured_name,
                detail_url=detail_url,
                community_id=link_match.group("community_id"),
            ),
        )
    return list(candidates.values())


def select_unique_candidate(candidates: list[CommunityCandidate], community_name: str) -> CommunityCandidate:
    """要求目标名称最终只对应一个小区详情 ID。"""
    matched = [item for item in candidates if community_name_match(community_name, item.community_name)]
    if not matched:
        raise RuntimeError(f"挂牌卡未找到目标小区：{community_name}")
    if len(matched) > 1:
        raise RuntimeError(
            f"目标小区对应多个乐有家详情 ID，拒绝自动选择："
            f"{[(item.community_id, item.community_name) for item in matched]}"
        )
    return matched[0]


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
    candidate_name: str,
    manual_login: bool,
) -> Optional[CommunityCandidate]:
    """按单个候选名搜索并提取唯一小区候选；未命中返回 None。"""
    search_url = f"{start_url}?c={quote(candidate_name)}"
    log.info("搜索 URL：%s", search_url)
    await page.get(search_url)
    await page
    await asyncio.sleep(3)
    result_html = await ensure_accessible(
        page,
        label=f"小区挂牌搜索页[{candidate_name}]",
        expected_host=expected_host,
        manual=manual_login,
    )
    await dump_html(page, f"lyj_community_page_search_{_safe_file_token(candidate_name)}")

    try:
        snapshots = lyj_parsers.parse_listing_snapshots(result_html)
        if not has_matching_community_snapshots(snapshots, candidate_name):
            log.info(
                "搜索名[%s]挂牌搜索结果房源未匹配目标小区：结构化房源=%d 条",
                candidate_name,
                len(snapshots),
            )
            return None

        actual_search_url = page.target.url or search_url
        return select_unique_candidate(
            extract_community_candidates(result_html, actual_search_url, candidate_name),
            candidate_name,
        )
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
    """处理单个小区：候选名兜底搜索 → 拼接挂牌列表入口并复核 → 返回结果摘要。"""
    names = build_candidate_names(city, administrative_district, community_name)
    candidate: Optional[CommunityCandidate] = None
    matched_name = ""
    for name in names:
        candidate = await search_and_extract_candidate(
            page,
            start_url=start_url,
            expected_host=expected_host,
            candidate_name=name,
            manual_login=manual_login,
        )
        if candidate is not None:
            matched_name = name
            break
    if candidate is None:
        raise RuntimeError(f"候选名 {names} 均未提取到目标小区唯一候选，拒绝自动选择")

    # c=名称只是搜索入口；b=小区 ID 才是乐有家的小区挂牌列表入口。
    listing_page_url = f"{start_url}?b={candidate.community_id}"
    await page.get(listing_page_url)
    await page
    await asyncio.sleep(2)
    listing_html = await ensure_accessible(
        page,
        label="小区挂牌页",
        expected_host=expected_host,
        manual=manual_login,
    )
    await dump_html(page, f"lyj_community_page_listing_{_safe_file_token(community_name)}")
    listing_snapshots = lyj_parsers.parse_listing_snapshots(listing_html)
    if not has_matching_community_snapshots(listing_snapshots, matched_name):
        raise RuntimeError(f"挂牌页复核未匹配目标小区：{matched_name}")
    actual_listing_url = page.target.url or listing_page_url
    actual_community_id = re.search(r"(?:[?&])b=(\d+)", actual_listing_url)
    if not actual_community_id or actual_community_id.group(1) != candidate.community_id:
        raise RuntimeError(
            f"挂牌页小区 ID 校验失败：期望 {candidate.community_id}，实际 URL={actual_listing_url}"
        )

    return {
        "city": city,
        "administrative_district": administrative_district,
        "community_district": community_district,
        "community_name": community_name,
        "matched_search_name": matched_name,
        "platform_name": candidate.community_name,
        "community_detail_url": candidate.detail_url,
        "listing_page_url": actual_listing_url,
        "structured_listing_count": len(listing_snapshots),
    }


async def main(
    city: str,
    administrative_district: str,
    community_names: list[str],
    community_district: str,
    manual_login: bool,
    debug: bool,
    manual_close: bool = True,
) -> list[dict]:
    """批量执行乐有家小区挂牌页 URL 初始化，单小区失败不中断。

    返回逐小区结果摘要（含 success 标记）；manual_close=False 时跳过结束前的
    人工确认回车，供统一初始化入口 scripts/initialize_community_page/init_community_pages.py 复用。
    """
    if debug:
        set_debug_mode(True)

    start_url = get_start_url("lyj", city)
    expected_host = _expected_host(start_url)
    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    try:
        page = await browser.get(start_url)
        await page
        await asyncio.sleep(3)
        await ensure_accessible(page, label="首页", expected_host=expected_host, manual=manual_login)
        await dump_html(page, "lyj_community_page_home")

        # 乐有家搜索需要已登录会话；人工登录确认必须发生在搜索之前。
        if manual_login:
            await wait_for_manual_login()
            await page.get(start_url)
            await page
            await asyncio.sleep(3)
            await ensure_accessible(page, label="登录后首页", expected_host=expected_host, manual=True)

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
                print(f"community_detail_url（辅助）：{summary['community_detail_url']}")
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
                    f"listing={item['listing_page_url']}"
                )
            else:
                print(f"[失败] {item['community_name']}：{item['error']}")
        log.info(
            "批量处理完成：共 %d 个，成功 %d 个",
            len(summaries),
            sum(1 for item in summaries if item["success"]),
        )
        if manual_close:
            await wait_for_manual_close()
    finally:
        browser.stop()

    return summaries


def cli() -> None:
    """解析命令行参数并启动脚本。"""
    parser = argparse.ArgumentParser(
        description="乐有家小区挂牌页 URL 初始化 MVP（批量，支持别名兜底）"
    )
    parser.add_argument("--city", default="深圳", help="城市名称")
    parser.add_argument("--administrative-district", required=True, help="主数据行政区，仅作输出上下文")
    parser.add_argument("--community-district", default="", help="主数据片区，仅作辅助输出")
    parser.add_argument(
        "--community",
        required=True,
        nargs="+",
        help="目标小区名称（可多个），支持主数据别名，未命中时自动按正式名/别名兜底重试",
    )
    parser.add_argument("--manual-login", action="store_true", help="验证码/登录拦截时等待人工处理")
    parser.add_argument("--debug", action="store_true", help="导出首页和挂牌页 HTML")
    args = parser.parse_args()
    uc.loop().run_until_complete(
        main(
            city=args.city,
            administrative_district=args.administrative_district,
            community_names=args.community,
            community_district=args.community_district,
            manual_login=args.manual_login,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    cli()
