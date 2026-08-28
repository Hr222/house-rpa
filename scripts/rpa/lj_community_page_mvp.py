# -*- coding: utf-8 -*-
"""链家小区挂牌页 URL 初始化 MVP（批量，支持主数据别名兜底）。

与 ``ke_community_page_mvp`` 同构：人工登录/验证 → 逐个小区搜索 → 从挂牌房源卡的
小区链接读取 xiaoqu ID → 拼接 ``/ershoufang/c{id}/`` → 实际打开并复核挂牌页。

链家与贝壳同代码库、DOM 高度相似，但保持独立脚本：平台 DOM 随时可能分叉，
差异点集中在两处——风控/登录检测走 lj_adapter.detect_block、行政区+片区取自
小区卡 agentCardResblockSubTitle（“福田区梅林”连写、无空格分隔）。
搜索与贝壳同款：直接导航 rs URL，不走真人点击搜索。

给定名未匹配到目标小区时，查询小区主数据（只读）换正式名与其余别名重试；
全部候选名失败才判定该小区失败。单个小区失败只记录并继续，不关闭浏览器；
浏览器仅在全部小区处理完并经人工确认后才退出。

本脚本只用于独立验证，不写入数据库，不点击小区筛选框，也不接入正式采集链路。

用法：
  python -m scripts.rpa.lj_community_page_mvp --manual-login \
      --city "深圳" --administrative-district "福田区" \
      --community "绿景虹湾" "香蜜湖一号"
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
from html import unescape
import logging
from pathlib import Path
import re
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import nodriver as uc

from app.community_data import resolve_communities
from app.rpa.core import config
from app.rpa.platforms.adapters import lj as lj_adapter
from app.rpa.platforms.base import community_name_match
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging


setup_logging()
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommunityCandidate:
    """小区链接候选（挂牌房源卡或小区卡发现）。"""

    community_name: str
    detail_url: str
    xiaoqu_id: str
    platform_administrative_district: str = ""
    platform_area: str = ""


def _visible_text(fragment: str) -> str:
    """提取 HTML 片段的可见文本。"""
    fragment = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(fragment)).strip()


def normalize_district(value: str) -> str:
    """统一“福田”和“福田区”等行政区写法。"""
    text = re.sub(r"\s+", "", str(value or ""))
    for suffix in ("行政区", "新区", "区"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _expected_host(start_url: str) -> str:
    return (urlparse(start_url).hostname or "").lower()


def blocked_reason(url: str, html: str, expected_host: str) -> Optional[str]:
    """识别链家登录、验证码和城市漂移。"""
    blocked, reason = lj_adapter.detect_block(url, html)
    if blocked:
        return reason
    actual_host = (urlparse(url or "").hostname or "").lower()
    if actual_host != expected_host:
        return f"城市漂移：期望 {expected_host}，实际 {actual_host or '空'}"
    return None


async def wait_for_manual_login() -> None:
    """搜索前固定等待人工完成登录/验证。"""
    await asyncio.to_thread(
        input,
        "\n请先在浏览器完成链家登录/验证，完成后回到终端按回车开始搜索小区...\n",
    )


async def ensure_accessible(page, *, label: str, expected_host: str, manual: bool) -> str:
    """页面被拦截时等待一次人工处理，复检失败则直接停止。"""
    await page
    html = await page.get_content()
    reason = blocked_reason(page.target.url or "", html, expected_host)
    if reason is None:
        return html
    if not manual:
        raise RuntimeError(f"{label}{reason}，请使用 --manual-login 后重试")
    log.warning("%s不可用：%s", label, reason)
    await asyncio.to_thread(input, f"\n{label}{reason}。请在浏览器处理完成后按回车继续...\n")
    await page
    await asyncio.sleep(2)
    html = await page.get_content()
    reason = blocked_reason(page.target.url or "", html, expected_host)
    if reason is not None:
        raise RuntimeError(f"{label}人工处理后仍不可用：{reason}")
    return html


async def dump_html(page, name: str) -> Optional[Path]:
    """在开启 --debug 时导出当前页面 HTML。"""
    return await shared_dump_html(page, name, logger=log)


async def _search_community(page, start_url: str, community_name: str) -> None:
    """按贝壳同款方式直接进入小区搜索结果页。

    链家与贝壳同代码库，rs URL 通用；曾试验 adapter 的真人点击搜索，
    登录后页面水合会冲掉刚输入的搜索词导致空词提交，故此处不采用。
    """
    search_url = urljoin(start_url, f"rs{quote(community_name, safe='')}/")
    log.info("使用链家小区搜索 URL：%s", search_url)
    await page.get(search_url)
    await page
    await page.select("ul.sellListContent", timeout=15)
    await asyncio.sleep(2)


def _listing_blocks(result_html: str) -> list[str]:
    """提取链家主挂牌列表的房源卡 HTML。"""
    source = result_html or ""
    match = re.search(
        r'<ul\b[^>]*class=["\'][^"\']*sellListContent[^"\']*["\'][^>]*>(?P<body>.*?)</ul>',
        source,
        re.I | re.S,
    )
    source = match.group("body") if match else source
    return [item.group(0) for item in re.finditer(r"<li\b(?P<body>.*?)</li>", source, re.I | re.S)]


def _candidate_from_block(block: str, result_url: str, community_name: str) -> Optional[CommunityCandidate]:
    """从单条挂牌房源卡的 positionInfo 小区链接提取 ID。

    链家房源卡 positionInfo 只有“小区名 - 片区”，不含行政区，
    因此这里不做行政区/片区提取，统一由小区卡 SubTitle 提供。
    """
    links = re.finditer(
        r'<a\b(?P<attrs>[^>]*?href=["\'](?P<href>[^"\']*/xiaoqu/(?P<xiaoqu_id>\d+)/?[^"\']*)["\'][^>]*)>(?P<body>.*?)</a>',
        block,
        re.I | re.S,
    )
    for link in links:
        captured_name = _visible_text(link.group("body"))
        if not community_name_match(community_name, captured_name):
            continue
        href = urljoin(result_url, unescape(link.group("href")))
        return CommunityCandidate(
            community_name=captured_name,
            detail_url=href,
            xiaoqu_id=link.group("xiaoqu_id"),
        )
    return None


def _candidate_from_agent_card(
    result_html: str, result_url: str, community_name: str
) -> Optional[CommunityCandidate]:
    """从顶部小区卡（agentCardResblockTitle/Link）提取 ID，挂牌数为 0 时的唯一来源。"""
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", result_html or "", re.I | re.S):
        attrs = match.group("attrs")
        if "agentCardResblockTitle" not in attrs and "agentCardResblockLink" not in attrs:
            continue
        href_match = re.search(
            r"\bhref\s*=\s*[\"'](?P<href>[^\"']*/xiaoqu/(?P<xiaoqu_id>\d+)/?[^\"']*)[\"']",
            attrs,
            re.I,
        )
        if not href_match:
            continue
        captured_name = _visible_text(match.group("body"))
        if not community_name_match(community_name, captured_name):
            continue
        href = urljoin(result_url, unescape(href_match.group("href")))
        return CommunityCandidate(
            community_name=captured_name,
            detail_url=href,
            xiaoqu_id=href_match.group("xiaoqu_id"),
        )
    return None


def _extract_agent_card_location(result_html: str) -> tuple[str, str]:
    """从小区卡 agentCardResblockSubTitle 拆出行政区与片区。

    链家为连写文本（如“福田区梅林”，无空格），与贝壳的空格分隔写法不同，
    不能复用贝壳版基于 \\s+ 的上下文正则。
    """
    match = re.search(
        r'<span\b[^>]*class=["\'][^"\']*agentCardResblockSubTitle[^"\']*["\'][^>]*>(?P<text>[^<]*)</span>',
        result_html or "",
        re.I | re.S,
    )
    text = re.sub(r"\s+", "", unescape(match.group("text"))) if match else ""
    if not text:
        return "", ""
    split = re.match(r"^(?P<district>.*?(?:区|新区))(?P<area>.*)$", text)
    if not split:
        log.warning("小区卡 SubTitle 无法拆分行政区/片区：%s", text)
        return "", ""
    return split.group("district"), split.group("area")


def extract_community_candidates(
    result_html: str, result_url: str, community_name: str
) -> tuple[list[CommunityCandidate], Optional[CommunityCandidate]]:
    """提取挂牌房源卡候选与小区卡候选（两条路径，供交叉印证）。"""
    candidates: dict[str, CommunityCandidate] = {}
    for block in _listing_blocks(result_html):
        candidate = _candidate_from_block(block, result_url, community_name)
        if candidate:
            candidates.setdefault(candidate.xiaoqu_id, candidate)
    agent_candidate = _candidate_from_agent_card(result_html, result_url, community_name)
    return list(candidates.values()), agent_candidate


def select_unique_candidate(
    listing_candidates: list[CommunityCandidate],
    agent_candidate: Optional[CommunityCandidate],
    community_name: str,
) -> CommunityCandidate:
    """要求目标小区最终只对应一个 xiaoqu ID，两条路径互相印证。"""
    matched = [
        item
        for item in listing_candidates
        if community_name_match(community_name, item.community_name)
    ]
    if not matched and agent_candidate is not None:
        log.info("挂牌房源卡未命中，使用小区卡候选（方式二兜底）：%s", agent_candidate.xiaoqu_id)
        matched = [agent_candidate]
    if not matched:
        raise RuntimeError(f"挂牌房源卡与小区卡均未找到目标小区链接：{community_name}")

    # 优先采用名称完全一致的候选，避免宽匹配误选。
    def name_key(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()

    exact = [item for item in matched if name_key(item.community_name) == name_key(community_name)]
    if exact:
        matched = exact
    if len(matched) > 1:
        raise RuntimeError(
            f"目标小区对应多个链家 xiaoqu ID，拒绝自动选择："
            f"{[(item.xiaoqu_id, item.community_name) for item in matched]}"
        )
    selected = matched[0]
    if (
        agent_candidate is not None
        and selected is not agent_candidate
        and agent_candidate.xiaoqu_id != selected.xiaoqu_id
    ):
        raise RuntimeError(
            f"两条路径 xiaoqu ID 不一致，拒绝自动选择："
            f"挂牌卡={selected.xiaoqu_id}，小区卡={agent_candidate.xiaoqu_id}"
        )
    return selected


def _safe_file_token(value: str) -> str:
    """把小区名转成 Windows 文件名可安全使用的片段。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(value or "").strip())


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


async def search_and_extract(
    page,
    start_url: str,
    expected_host: str,
    candidate_name: str,
    manual_login: bool,
) -> Optional[CommunityCandidate]:
    """按单个候选名搜索并提取唯一小区候选（含两条路径印证）；未命中返回 None。"""
    await _search_community(page, start_url, candidate_name)
    result_html = await ensure_accessible(
        page,
        label=f"小区挂牌搜索页[{candidate_name}]",
        expected_host=expected_host,
        manual=manual_login,
    )
    result_url = page.target.url or start_url
    result_path = urlparse(result_url).path
    if "/ershoufang/rs" not in result_path:
        raise RuntimeError(
            f"小区搜索未真正提交，未进入 /ershoufang/rs.../：{result_url or '空 URL'}"
        )
    await dump_html(page, f"lj_community_page_search_{_safe_file_token(candidate_name)}")
    listing_candidates, agent_candidate = extract_community_candidates(
        result_html, result_url, candidate_name
    )
    try:
        candidate = select_unique_candidate(listing_candidates, agent_candidate, candidate_name)
    except RuntimeError as exc:
        log.info("搜索名[%s]未提取到目标小区唯一候选：%s", candidate_name, exc)
        return None
    district, area = _extract_agent_card_location(result_html)
    return replace(
        candidate, platform_administrative_district=district, platform_area=area
    )


async def collect_community(
    page,
    *,
    start_url: str,
    city: str,
    expected_host: str,
    administrative_district: str,
    community_district: str,
    community_name: str,
    manual_login: bool,
) -> dict:
    """处理单个小区：候选名兜底搜索 → 打开挂牌页复核 → 返回结果摘要。"""
    names = build_candidate_names(city, administrative_district, community_name)
    candidate: Optional[CommunityCandidate] = None
    matched_name = ""
    for name in names:
        candidate = await search_and_extract(page, start_url, expected_host, name, manual_login)
        if candidate is not None:
            matched_name = name
            break
    if candidate is None:
        raise RuntimeError(f"候选名 {names} 均未提取到目标小区唯一挂牌卡，拒绝自动选择")

    log.info("挂牌卡已提取 xiaoqu_id=%s，平台小区名称=%s", candidate.xiaoqu_id, candidate.community_name)
    listing_page_url = urljoin(start_url, f"c{candidate.xiaoqu_id}/")
    await page.get(listing_page_url)
    await page
    await asyncio.sleep(3)
    await ensure_accessible(
        page,
        label="小区挂牌页",
        expected_host=expected_host,
        manual=manual_login,
    )
    await dump_html(page, f"lj_community_page_listing_{_safe_file_token(community_name)}")

    actual_url = page.target.url or listing_page_url
    actual_id = re.search(r"/ershoufang/c(\d+)/", urlparse(actual_url).path)
    if not actual_id or actual_id.group(1) != candidate.xiaoqu_id:
        raise RuntimeError(
            f"挂牌页 xiaoqu ID 校验失败：期望 {candidate.xiaoqu_id}，实际 URL={actual_url}"
        )

    expected_district = normalize_district(administrative_district)
    platform_district = normalize_district(candidate.platform_administrative_district)
    if platform_district and platform_district != expected_district:
        raise RuntimeError(
            f"小区卡行政区不匹配：主数据={administrative_district}，平台={candidate.platform_administrative_district}"
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
        "xiaoqu_id": candidate.xiaoqu_id,
        "community_detail_url": candidate.detail_url,
        "listing_page_url": actual_url,
    }


async def main(
    city: str,
    administrative_district: str,
    community_names: list[str],
    community_district: str,
    manual_login: bool,
    debug: bool,
) -> None:
    """批量执行链家小区挂牌页 URL 初始化，单小区失败不中断。"""
    if debug:
        set_debug_mode(True)

    start_url = get_start_url("lj", city)
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
        await dump_html(page, "lj_community_page_home")

        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(start_url)
            await page
            await asyncio.sleep(3)
            await ensure_accessible(page, label="登录后首页", expected_host=expected_host, manual=False)
        else:
            await ensure_accessible(page, label="首页", expected_host=expected_host, manual=False)

        summaries: list[dict] = []
        for community_name in community_names:
            try:
                summary = await collect_community(
                    page,
                    start_url=start_url,
                    city=city,
                    expected_host=expected_host,
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
                print(f"xiaoqu_id：{summary['xiaoqu_id']}")
                print(f"community_detail_url（辅助）：{summary['community_detail_url']}")
                print(f"listing_page_url：{summary['listing_page_url']}")
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
                    f"[成功] {item['community_name']}：平台名={item['platform_name']}，"
                    f"xiaoqu_id={item['xiaoqu_id']}，listing={item['listing_page_url']}"
                )
            else:
                print(f"[失败] {item['community_name']}：{item['error']}")
        log.info(
            "批量处理完成：共 %d 个，成功 %d 个",
            len(summaries),
            sum(1 for item in summaries if item["success"]),
        )
        log.info("链家挂牌页已打开，交由人工确认页面内容")
        await asyncio.to_thread(
            input, "\n全部小区已处理完成。你确认无误后按回车结束脚本（浏览器将关闭）...\n"
        )
    finally:
        browser.stop()


def cli() -> None:
    """解析命令行参数并启动脚本。"""
    parser = argparse.ArgumentParser(description="链家小区挂牌页 URL 初始化 MVP（批量，支持别名兜底）")
    parser.add_argument("--city", default="深圳", help="城市名称")
    parser.add_argument("--administrative-district", required=True, help="主数据行政区")
    parser.add_argument("--community-district", default="", help="主数据片区，仅作辅助输出")
    parser.add_argument(
        "--community",
        required=True,
        nargs="+",
        help="目标小区名称（可多个），支持主数据别名，未命中时自动按正式名/别名兜底重试",
    )
    parser.add_argument("--manual-login", action="store_true", help="搜索前等待人工登录/验证")
    parser.add_argument("--debug", action="store_true", help="导出首页、搜索页和挂牌页 HTML")
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
