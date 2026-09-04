# -*- coding: utf-8 -*-
"""贝壳小区挂牌页 URL 初始化 MVP（批量，支持主数据别名兜底）。

流程：人工登录/验证 → 逐个小区搜索 → 从挂牌房源卡的小区链接读取 xiaoqu ID →
拼接 ``/ershoufang/c{id}/`` → 实际打开并复核挂牌页。

给定名未匹配到目标小区时，查询小区主数据（只读）换正式名与其余别名重试；
全部候选名失败才判定该小区失败。单个小区失败只记录并继续，不关闭浏览器；
浏览器仅在全部小区处理完并经人工确认后才退出。

本脚本只用于独立验证，不写入数据库，不点击小区筛选框，也不接入正式采集链路。

用法：
  python -m scripts.initialize_community_page.community_page.ke_community_page_mvp --manual-login \
      --city "深圳" --administrative-district "福田区" \
      --community "中海华庭" "香蜜湖一号"
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
from app.rpa.platforms.ke import collector as ke_adapter
from app.rpa.platforms.base import community_name_match, wait_and_reload_after_block
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.debug_utils import dump_html as shared_dump_html
from app.rpa.utils.debug_utils import set_debug_mode
from app.rpa.utils.logging_utils import setup_logging


setup_logging()
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommunityCandidate:
    """挂牌房源卡中发现的小区链接。"""

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


async def wait_for_manual_login() -> None:
    """搜索前固定等待人工完成登录/验证。"""
    await asyncio.to_thread(
        input,
        "\n请先在浏览器完成贝壳登录/验证，完成后回到终端按回车开始搜索小区...\n",
    )


async def ensure_accessible(page, *, label: str, expected_host: str) -> str:
    """风控恢复走工程协议（检测→等人工回车→重取，直到恢复），恢复后校验城市域名。

    风控判定=ke 工程 detect_block + base 公共兜底（wait_and_reload_after_block
    内部统一叠加 detect_block_with_common）。城市漂移（期望域名不符）是 MVP
    导航层职责，不属于风控 marker，恢复后在此单独校验。
    """
    html = await wait_and_reload_after_block(page, ke_adapter.detect_block, label)
    actual_host = (urlparse(page.target.url or "").hostname or "").lower()
    if actual_host != expected_host:
        raise RuntimeError(
            f"{label}城市漂移：期望 {expected_host}，实际 {actual_host or '空'}"
        )
    return html


async def dump_html(page, name: str) -> Optional[Path]:
    """在开启 --debug 时导出当前页面 HTML。"""
    return await shared_dump_html(page, name, logger=log)


async def _is_interactable(element) -> bool:
    """过滤隐藏的重复搜索框。"""
    try:
        position = await element.get_position()
        return bool(position and position.width > 0 and position.height > 0)
    except Exception:
        return False


async def _search_community(page, start_url: str, community_name: str) -> None:
    """按已确认的贝壳小区搜索路径进入结果页。"""
    search_url = urljoin(start_url, f"rs{quote(community_name, safe='')}/")
    log.info("使用贝壳小区搜索 URL：%s", search_url)
    await page.get(search_url)
    await page
    await page.select("ul.sellListContent", timeout=15)
    await asyncio.sleep(2)


def _listing_blocks(result_html: str) -> list[str]:
    """提取贝壳主挂牌列表的房源卡 HTML。"""
    source = result_html or ""
    match = re.search(
        r'<ul\b[^>]*class=["\'][^"\']*sellListContent[^"\']*["\'][^>]*>(?P<body>.*?)</ul>',
        source,
        re.I | re.S,
    )
    source = match.group("body") if match else source
    return [item.group(0) for item in re.finditer(r"<li\b(?P<body>.*?)</li>", source, re.I | re.S)]


def _candidate_from_block(block: str, result_url: str, community_name: str) -> Optional[CommunityCandidate]:
    """从单条挂牌房源卡的 positionInfo 小区链接提取 ID。"""
    links = re.finditer(
        r'<a\b(?P<attrs>[^>]*?href=["\'](?P<href>[^"\']*/xiaoqu/(?P<xiaoqu_id>\d+)/?[^"\']*)["\'][^>]*)>(?P<body>.*?)</a>',
        block,
        re.I | re.S,
    )
    for link in links:
        attrs = link.group("attrs")
        captured_name = _visible_text(link.group("body"))
        if not community_name_match(community_name, captured_name):
            continue

        context = _visible_text(block)
        platform_district = ""
        platform_area = ""
        # 兼容贝壳小区信息卡中的“福田区 梅林”文本；仅用于输出/辅助核对。
        location_match = re.search(
            r"(?P<district>[\u4e00-\u9fffA-Za-z0-9]{1,12}(?:区|新区))\s+"
            r"(?P<area>[\u4e00-\u9fffA-Za-z0-9]{1,20})",
            context,
        )
        if location_match:
            platform_district = location_match.group("district")
            platform_area = location_match.group("area")

        href = urljoin(result_url, unescape(link.group("href")))
        return CommunityCandidate(
            community_name=captured_name,
            detail_url=href,
            xiaoqu_id=link.group("xiaoqu_id"),
            platform_administrative_district=platform_district,
            platform_area=platform_area,
        )
    return None


def extract_community_candidates(result_html: str, result_url: str, community_name: str) -> list[CommunityCandidate]:
    """从挂牌房源卡或无房源时的小区详情卡提取小区链接。"""
    candidates: dict[str, CommunityCandidate] = {}
    for block in _listing_blocks(result_html):
        candidate = _candidate_from_block(block, result_url, community_name)
        if candidate:
            candidates.setdefault(candidate.xiaoqu_id, candidate)

    # 挂牌数为 0 时没有房源 li，但页面仍有 agentCardResblockTitle 小区详情卡。
    if not candidates:
        for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", result_html or "", re.I | re.S):
            attrs = match.group("attrs")
            if "agentCardResblockTitle" not in attrs:
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
            candidates.setdefault(
                href_match.group("xiaoqu_id"),
                CommunityCandidate(
                    community_name=captured_name,
                    detail_url=href,
                    xiaoqu_id=href_match.group("xiaoqu_id"),
                ),
            )
    return list(candidates.values())


def select_unique_candidate(candidates: list[CommunityCandidate], community_name: str) -> CommunityCandidate:
    """要求目标小区最终只对应一个 xiaoqu ID。"""
    matched = [item for item in candidates if community_name_match(community_name, item.community_name)]
    if not matched:
        raise RuntimeError(f"挂牌房源卡未找到目标小区链接：{community_name}")

    # 优先采用名称完全一致的候选，避免“东方银座公馆”被宽匹配为“东方银座”。
    def name_key(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()

    exact = [item for item in matched if name_key(item.community_name) == name_key(community_name)]
    if exact:
        matched = exact
    if len(matched) > 1:
        raise RuntimeError(
            f"目标小区对应多个贝壳 xiaoqu ID，拒绝自动选择："
            f"{[(item.xiaoqu_id, item.community_name) for item in matched]}"
        )
    return matched[0]


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
) -> Optional[CommunityCandidate]:
    """按单个候选名搜索并提取唯一小区候选；未命中返回 None。"""
    await _search_community(page, start_url, candidate_name)
    result_html = await ensure_accessible(
        page,
        label=f"小区挂牌搜索页[{candidate_name}]",
        expected_host=expected_host,
    )
    result_url = page.target.url or start_url
    result_path = urlparse(result_url).path
    if "/ershoufang/rs" not in result_path:
        raise RuntimeError(
            f"小区搜索未真正提交，未进入 /ershoufang/rs.../：{result_url}"
        )
    await dump_html(page, f"ke_community_page_search_{_safe_file_token(candidate_name)}")
    try:
        return select_unique_candidate(
            extract_community_candidates(result_html, result_url, candidate_name),
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
) -> dict:
    """处理单个小区：候选名兜底搜索 → 打开挂牌页复核 → 返回结果摘要。"""
    names = build_candidate_names(city, administrative_district, community_name)
    candidate: Optional[CommunityCandidate] = None
    matched_name = ""
    for name in names:
        candidate = await search_and_extract(page, start_url, expected_host, name)
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
    )
    await dump_html(page, f"ke_community_page_listing_{_safe_file_token(community_name)}")

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
            f"挂牌卡行政区不匹配：主数据={administrative_district}，平台={candidate.platform_administrative_district}"
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
    manual_close: bool = True,
) -> list[dict]:
    """批量执行贝壳小区挂牌页 URL 初始化，单小区失败不中断。

    返回逐小区结果摘要（含 success 标记）；manual_close=False 时跳过结束前的
    人工确认回车，供统一初始化入口 scripts/initialize_community_page/init_community_pages.py 复用。
    """
    if debug:
        set_debug_mode(True)

    start_url = get_start_url("ke", city)
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
        await dump_html(page, "ke_community_page_home")

        if manual_login:
            await wait_for_manual_login()
            page = await browser.get(start_url)
            await page
            await asyncio.sleep(3)
            await ensure_accessible(page, label="登录后首页", expected_host=expected_host)
        else:
            await ensure_accessible(page, label="首页", expected_host=expected_host)

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
        log.info("贝壳挂牌页已打开，交由人工确认页面内容")
        if manual_close:
            await asyncio.to_thread(
                input, "\n全部小区已处理完成。你确认无误后按回车结束脚本（浏览器将关闭）...\n"
            )
    finally:
        browser.stop()

    return summaries


def cli() -> None:
    """解析命令行参数并启动脚本。"""
    parser = argparse.ArgumentParser(description="贝壳小区挂牌页 URL 初始化 MVP（批量，支持别名兜底）")
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
