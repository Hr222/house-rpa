# -*- coding: utf-8 -*-
"""链家小区挂牌页 URL 初始化 MVP——匿名 headerSearch API 版（无浏览器、无登录）。

背景：浏览器+账号版 ``lj_community_page_mvp`` 在批量初始化中频繁触发风控并已
发生账号封禁（该脚本 2026-09-30 已删除，不留兜底），链家小区入口初始化统一走
本模块（解析核与本文件同处，一平台一文件，与 fang 模块结构一致）。

数据源：链家 ``/api/headerSearch`` 联想接口，匿名可调（仅需 UA/Referer），返回
候选小区的 communityId / title / region / 在售数，且 region 括号内携带别名。
不实开挂牌页/成交页复核（避免页面请求暴露自动化特征），入口 URL 按"挂牌/成交/
小区详情共用 id"的链家惯例派生，格式与库内既有数据一致。

匹配规则（宁可缺不错配）：
- 候选名依次尝试：给定名 → 主数据正式名 → 其余别名（同一主数据来源）；
- 单个候选名的命中条件：region 行政区与主数据一致，且 title 归一化全等、
  候选名为 title 子串、或候选名出现在 region 别名括号内；
- 采纳优先级一：title 归一化全等的命中若唯一（communityId 唯一），直接采纳
  ——别名/子串撞车不影响精确命中；
- 采纳优先级二：否则要求全部命中收敛到唯一 communityId，多个即记歧义并换
  下一个候选名；全部候选名失败才判定该小区失败，错误信息附候选明细供人工裁决
  （桃源居综合楼、领航城三期这类需人工映射的条目会在此显式暴露）。

签名与结果字段与统一入口 ``init_community_pages`` 的 ``PLATFORM_MODULES["lj"]``
落库链路（save_community_platform_pages）完全对齐，统一入口零改动。

用法（独立运行）：
  python -m scripts.initialize_community_page.community_page.lj_community_page_mvp \
      --city "深圳" --administrative-district "福田区" --community "绿景虹湾" "香蜜湖一号"
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import time
from typing import Optional
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from app.community_data import resolve_communities
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.logging_utils import setup_logging

setup_logging()
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# headerSearch 的城市 cityId；当前初始化清单仅深圳，扩城时在此登记。
CITY_IDS = {"深圳": "440300"}

REQUEST_INTERVAL_SECONDS = 0.4
REQUEST_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def normalize_district(value: str) -> str:
    """统一“福田”和“福田区”等行政区写法。"""
    text = re.sub(r"\s+", "", str(value or ""))
    for suffix in ("行政区", "新区", "区"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def name_key(value: str) -> str:
    """名称归一化键：仅保留字母/数字/汉字并转小写。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


# ── 主干词换问法（提高联想接口召回；命中会标记为 *_stem 便于审计）──
_TYPE_SUFFIXES = (
    "大厦", "公寓", "花园", "新村", "宿舍楼", "宿舍", "广场", "中心", "大楼",
    "综合楼", "住宅楼", "家园", "小区", "公馆", "别墅",
    "一期", "二期", "三期", "四期", "五期", "六期", "七期", "八期",
    "A区", "B区", "C区", "D区",
)
_STEM_QUERY_SUFFIXES = (
    "山庄", "大院", "别墅", "大厦", "公寓", "花园", "新村", "宿舍楼", "宿舍",
    "广场", "中心", "大楼", "综合楼", "住宅楼", "家园", "小区", "公馆",
    "苑", "阁", "庭", "轩", "居", "邨", "村", "城", "园", "府", "坊", "里",
    "台", "湾", "岛", "岸",
    "一期", "二期", "三期", "四期", "五期", "六期", "七期", "八期",
    "A区", "B区", "C区", "D区",
)

_REGION_TOKENS = ("宝安", "龙岗", "南山", "罗湖", "福田", "盐田", "龙华",
                  "光明", "坪山", "大鹏", "深圳")


def stem_variants(value: str) -> list[str]:
    """由名称派生主干词：先按分隔符切出主名，再剥期数/类型后缀与内嵌行政区字样。

    实测「怡景花园-景庭苑」原名搜不到，用主干词「怡景花园」能命中母小区页
    （同页映射），与 fang / lyj 侧处置一致。返回值**不含原名自身**。
    """
    base = re.sub(r"\s+", "", str(value or ""))
    if not base:
        return []
    parts = [item for item in re.split(r"[-–—·、,，()（）/|;；]+", base) if len(item) >= 2]
    out: list[str] = list(parts[:2])
    for item in [base, *parts]:
        for suffix in _STEM_QUERY_SUFFIXES:
            if item.endswith(suffix) and len(item) - len(suffix) >= 2:
                out.append(item[: -len(suffix)])
        for token in _REGION_TOKENS:
            if token in item and len(item) > len(token) + 1:
                out.append(item.replace(token, ""))
    return [item for item in dict.fromkeys(out) if len(item) >= 2 and item != base]


@dataclass(frozen=True)
class SuggestEntry:
    """headerSearch 返回的单条小区联想项。"""

    community_id: str
    title: str
    region: str
    on_sale_count: Optional[int]
    aliases: tuple[str, ...] = ()
    area: str = ""
    administrative_district: str = ""


def _parse_region(region: str) -> tuple[tuple[str, ...], str, str]:
    """把 region 拆成（别名, 片区, 行政区）。

    实测格式为“(别名1,别名2) 片区 行政区”：括号与片区可缺省、空白长度不定，
    别名分隔符可能是中英文逗号或顿号。
    """
    text = re.sub(r"\s+", " ", str(region or "")).strip()
    aliases: list[str] = []
    rest = text
    match = re.match(r"^[（(](?P<inner>[^（()）]*)[）)]\s*(?P<tail>.*)$", text)
    if match:
        aliases = [
            part.strip()
            for part in re.split(r"[,，、;；]", match.group("inner"))
            if part.strip()
        ]
        rest = match.group("tail").strip()
    parts = rest.split(" ")
    if len(parts) >= 2:
        return tuple(aliases), parts[0], parts[-1]
    if len(parts) == 1:
        return tuple(aliases), "", parts[0]
    return tuple(aliases), "", ""


def _parse_entry(row: dict) -> Optional[SuggestEntry]:
    """把单条接口记录转成联想项；无 communityId 的条目丢弃。"""
    community_id = row.get("communityId")
    if community_id is None or not str(community_id).strip():
        return None
    aliases, area, district = _parse_region(str(row.get("region") or ""))
    count = row.get("count")
    return SuggestEntry(
        community_id=str(community_id).strip(),
        title=str(row.get("title") or "").strip(),
        region=str(row.get("region") or ""),
        on_sale_count=int(count) if isinstance(count, (int, float)) else None,
        aliases=aliases,
        area=area,
        administrative_district=district,
    )


def _extract_result_rows(payload: dict) -> list[dict]:
    """兼容 data.data.result 与 data.result 两种响应嵌套。"""
    inner = payload.get("data") or {}
    result = inner.get("data") or inner
    rows = result.get("result") if isinstance(result, dict) else None
    return rows if isinstance(rows, list) else []


def _dump_debug(keyword: str, payload: dict) -> None:
    """--debug 时导出 headerSearch 原始响应，便于核对接口结构变化。"""
    directory = PROJECT_ROOT / "results" / "community_page" / "lj_api_debug"
    directory.mkdir(parents=True, exist_ok=True)
    token = re.sub(r'[\\/:*?"<>|\s]+', "_", keyword)[:40]
    path = directory / f"{time.strftime('%Y%m%d_%H%M%S')}_{token}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("已导出 headerSearch 原始响应：%s", path)


async def fetch_suggest(
    keyword: str, *, origin: str, city_id: str, debug: bool = False
) -> list[SuggestEntry]:
    """匿名调用 headerSearch 联想接口，返回候选小区列表（带限速与重试）。"""
    url = (
        f"{origin}/api/headerSearch?channel=ershoufang&cityId={city_id}"
        f"&keyword={quote(keyword, safe='')}"
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{origin}/ershoufang/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    def _request() -> dict:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
        try:
            payload = await asyncio.to_thread(_request)
            entries = [
                entry
                for entry in (_parse_entry(row) for row in _extract_result_rows(payload))
                if entry is not None
            ]
            if debug:
                _dump_debug(keyword, payload)
            return entries
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            log.warning(
                "headerSearch 第 %d/%d 次调用失败（keyword=%s）：%s",
                attempt, MAX_ATTEMPTS, keyword, exc,
            )
            await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"headerSearch 连续 {MAX_ATTEMPTS} 次调用失败：{last_error}")


def _match_entries(
    entries: list[SuggestEntry], keyword: str, administrative_district: str
) -> list[SuggestEntry]:
    """按行政区 + 名称/别名规则筛出命中项。

    行政区归一化后严格相等；名称命中 = title 归一化全等或候选名为 title 子串；
    别名命中 = 候选名出现在 region 别名括号内。
    """
    expected = normalize_district(administrative_district)
    hits: list[SuggestEntry] = []
    for entry in entries:
        if normalize_district(entry.administrative_district) != expected:
            continue
        title_hit = name_key(entry.title) == name_key(keyword) or keyword in entry.title
        alias_hit = any(keyword in alias for alias in entry.aliases)
        if title_hit or alias_hit:
            hits.append(entry)
    return hits


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
    # 主干词兜底（剥期数/后缀/行政区字样），排最后、精确度最低
    for base in list(names):
        for stem in stem_variants(base):
            if stem not in names:
                names.append(stem)
    log.info("小区[%s]候选搜索名：%s", community_name, names)
    return names


async def resolve_community(
    *,
    city: str,
    administrative_district: str,
    community_name: str,
    city_id: str,
    origin: str,
    debug: bool = False,
) -> dict:
    """解析单个小区：候选名逐个 API 匹配，返回平台无关的解析结果。

    返回字段：matched_search_name / platform_name / platform_district /
    platform_area / xiaoqu_id / match_via / on_sale_count。全部候选名失败时
    抛 RuntimeError，错误信息附候选明细供人工裁决；headerSearch 网络层连续
    失败同样抛 RuntimeError。
    """
    names = build_candidate_names(city, administrative_district, community_name)
    # 主干词集合：用于把「跨粒度命中」标记成 *_stem，便于事后审计
    stem_set = {stem for base in names for stem in stem_variants(base)}
    candidates_seen: dict[str, str] = {}
    for name in names:
        entries = await fetch_suggest(name, origin=origin, city_id=city_id, debug=debug)
        hits = _match_entries(entries, name, administrative_district)
        for entry in hits:
            on_sale = entry.on_sale_count if entry.on_sale_count is not None else "未知"
            candidates_seen.setdefault(
                entry.community_id, f"{entry.title}（{entry.region}，在售{on_sale}）"
            )
        chosen: Optional[SuggestEntry] = None
        match_via = ""
        # 优先级一：title 归一化全等命中唯一时直接采纳，别名/子串撞车不影响精确命中。
        exact_hits = [entry for entry in hits if name_key(entry.title) == name_key(name)]
        if exact_hits and len({entry.community_id for entry in exact_hits}) == 1:
            chosen = exact_hits[0]
            match_via = "title_exact"
        # 优先级二：其余命中必须收敛到唯一 communityId，多个即歧义。
        elif hits and len({entry.community_id for entry in hits}) == 1:
            chosen = hits[0]
            match_via = (
                "title"
                if (name_key(chosen.title) == name_key(name) or name in chosen.title)
                else "alias"
            )
        if chosen is not None:
            if name in stem_set:
                match_via = f"{match_via}_stem"      # 主干词命中（跨粒度映射）
            log.info(
                "候选名[%s]唯一命中 xiaoqu_id=%s 平台名=%s（%s）",
                name, chosen.community_id, chosen.title, match_via,
            )
            return {
                "matched_search_name": name,
                "platform_name": chosen.title,
                "platform_district": chosen.administrative_district or "未识别",
                "platform_area": chosen.area or "未识别",
                "xiaoqu_id": chosen.community_id,
                "match_via": match_via,
                "on_sale_count": chosen.on_sale_count,
            }
        if hits:
            log.warning(
                "候选名[%s]命中 %d 个小区，拒绝自动选择：%s",
                name, len({entry.community_id for entry in hits}),
                sorted({entry.community_id for entry in hits}),
            )
    detail = "；".join(f"{cid}: {desc}" for cid, desc in list(candidates_seen.items())[:10])
    suffix = "（仅列前 10 条）" if len(candidates_seen) > 10 else ""
    raise RuntimeError(f"候选名 {names} 均未唯一命中目标小区；候选明细：{detail or '无'}{suffix}")


async def collect_community(
    *,
    lj_origin: str,
    city: str,
    administrative_district: str,
    community_district: str,
    community_name: str,
    city_id: str,
    debug: bool,
) -> dict:
    """处理单个小区：解析出 xiaoqu_id 后按链家域名派生入口 URL。"""
    resolved = await resolve_community(
        city=city,
        administrative_district=administrative_district,
        community_name=community_name,
        city_id=city_id,
        origin=lj_origin,
        debug=debug,
    )
    xiaoqu_id = resolved["xiaoqu_id"]
    return {
        "city": city,
        "administrative_district": administrative_district,
        "community_district": community_district,
        "community_name": community_name,
        "matched_search_name": resolved["matched_search_name"],
        "platform_name": resolved["platform_name"],
        "platform_district": resolved["platform_district"],
        "platform_area": resolved["platform_area"],
        "xiaoqu_id": xiaoqu_id,
        "community_detail_url": f"{lj_origin}/xiaoqu/{xiaoqu_id}/",
        "listing_page_url": f"{lj_origin}/ershoufang/c{xiaoqu_id}",
        "match_via": resolved["match_via"],
        "on_sale_count": resolved["on_sale_count"],
    }


async def main(
    city: str,
    administrative_district: str,
    community_names: list[str],
    community_district: str = "",
    manual_login: bool = False,
    debug: bool = False,
    manual_close: bool = True,
    include_deal: bool = False,
) -> list[dict]:
    """批量执行链家小区入口初始化（匿名 API 版），单小区失败不中断。

    签名与统一入口 ``init_community_pages`` 一致：manual_login / manual_close 为
    兼容参数（API 模式无浏览器与登录态，自动忽略）；include_deal=True 时按同 id
    惯例派生成交页 URL。返回逐小区结果摘要（含 success 标记），字段与落库契约一致。
    """
    if manual_login:
        log.info("API 模式无登录态，忽略 manual_login 参数")
    start_url = get_start_url("lj", city)
    parsed = urlparse(start_url)
    lj_origin = f"{parsed.scheme}://{(parsed.hostname or '').lower()}"
    city_id = CITY_IDS.get(city)

    summaries: list[dict] = []
    for community_name in community_names:
        if not city_id:
            error = f"未登记城市[{city}]的链家 cityId（见 CITY_IDS）"
            log.error("小区[%s]处理失败：%s", community_name, error)
            summaries.append({"community_name": community_name, "success": False, "error": error})
            print(f"\n[失败] {community_name} → {error}")
            continue
        try:
            summary = await collect_community(
                lj_origin=lj_origin,
                city=city,
                administrative_district=administrative_district,
                community_district=community_district,
                community_name=community_name,
                city_id=city_id,
                debug=debug,
            )
            summary["success"] = True
            if include_deal:
                summary["deal_page_url"] = f"{lj_origin}/chengjiao/c{summary['xiaoqu_id']}/"
            summaries.append(summary)
            print(f"\n[成功] {community_name}")
            print(f"城市：{summary['city']}")
            print(f"行政区：{summary['administrative_district']}")
            print(f"片区（辅助）：{summary['community_district'] or '未提供'}")
            print(f"命中搜索名：{summary['matched_search_name']}")
            print(f"平台小区名称：{summary['platform_name']}（{summary['match_via']}命中，在售{summary['on_sale_count'] if summary['on_sale_count'] is not None else '未知'}）")
            print(f"平台行政区：{summary['platform_district']}")
            print(f"平台片区：{summary['platform_area']}")
            print(f"xiaoqu_id：{summary['xiaoqu_id']}")
            print(f"community_detail_url（辅助）：{summary['community_detail_url']}")
            print(f"listing_page_url：{summary['listing_page_url']}")
            if summary.get("deal_page_url"):
                print(f"deal_page_url：{summary['deal_page_url']}")
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
    return summaries


def cli() -> None:
    """解析命令行参数并启动脚本。"""
    parser = argparse.ArgumentParser(
        description="链家小区挂牌页 URL 初始化 MVP（匿名 headerSearch API 版，无浏览器无登录）"
    )
    parser.add_argument("--city", default="深圳", help="城市名称")
    parser.add_argument("--administrative-district", required=True, help="主数据行政区")
    parser.add_argument("--community-district", default="", help="主数据片区，仅作辅助输出")
    parser.add_argument(
        "--community",
        required=True,
        nargs="+",
        help="目标小区名称（可多个），支持主数据别名，未命中时自动按正式名/别名兜底重试",
    )
    parser.add_argument("--manual-login", action="store_true", help="兼容参数：API 模式无需登录，忽略")
    parser.add_argument("--debug", action="store_true", help="导出 headerSearch 原始响应 JSON")
    args = parser.parse_args()
    asyncio.run(
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
