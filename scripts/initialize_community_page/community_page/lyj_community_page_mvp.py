# -*- coding: utf-8 -*-
"""乐有家小区挂牌页 URL 初始化 MVP——匿名联想接口版（无浏览器、无登录）。

数据源：乐有家 ``POST /esf/queryConditionAndContext``（body ``keyword=<小区名>``，
**匿名可调，无需登录态**）。给**完整小区名**时返回小区条目，含
``id / areaName(行政区) / placeName(片区) / sellCount(在售)``；给模糊词时返回的是
筛选条件（城区/片区/地铁，``type`` 为 1/2/4），本模块按小区条目（``type`` 为 0）解析。

URL 派生（社区页初始化**只落入口**，成交不做）：
- 入口：``{origin}/esf?b={id}``（与库内既有 lyj 数据格式一致，``/esf`` 后无斜杠）。
  该页需登录才能浏览，但本模块**只派生不打开**，故无需登录态。
- 详情：``{origin}/xq/detail/{id}.html``（纯 HTTP 可访问，仅作输出辅助，不落库）。
- 成交：**不做**。乐有家不对外展示成交（``app/rpa/platforms/lyj/collector.py`` 既定口径：
  成交是登录/扫码后的弹层，无独立 URL），故本平台不落 ``deal_page_url``。

为什么是这条接口（2026-10-08 实测）：PC 站无 ``/chengjiao/`` 路径；入口页 ``?b=`` 需登录；
而该联想接口匿名可用，且返回的小区 id 与库内既有 lyj 入口的 ``b=`` 参数同源。

匹配规则（宁可缺不错配）：
- 候选名依次尝试：给定名 → 主数据正式名 → 其余别名；
- 单个候选名的命中条件：``areaName`` 行政区与主数据一致，且名称命中——
  归一化全等、**后缀差异**（如 ``飞扬时代大厦`` ↔ 乐有家 ``飞扬时代``）、
  或**汉字数字与阿拉伯数字差异**（如 ``桃源居十四区`` ↔ ``桃源居14区``）；
- 采纳：全等命中唯一 → 直接采纳；否则要求命中收敛到唯一 id，多个即记歧义并换下一个候选名；
  全部候选名失败才判定失败，错误信息附候选明细供人工裁决。

签名与结果字段与统一入口 ``init_community_pages`` 的 ``PLATFORM_MODULES["lyj"]``
落库链路（save_community_platform_pages）对齐：落库只依赖 ``success`` 与 ``listing_page_url``。

用法（独立运行，签名与统一入口一致）：
  python -m scripts.initialize_community_page.community_page.lyj_community_page_mvp \
      --city "深圳" --administrative-district "宝安区" --community "凤凰领寓" "宝雅苑"
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gzip
import json
import logging
import re
import time
from typing import Optional
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from app.community_data import resolve_communities
from app.rpa.platforms.city_map import get_start_url

log = logging.getLogger(__name__)

REQUEST_INTERVAL_SECONDS = 0.4
REQUEST_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
# 连续失败到此数即判定被风控拦截并中止本批（剩余小区不记为失败）。
MAX_CONSECUTIVE_FAILURES = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# 联想接口路径（相对城市站根）；入口/详情页由 id 派生。
SUGGEST_PATH = "/esf/queryConditionAndContext"
ENTRY_URL_TEMPLATE = "{origin}/esf?b={xiaoqu_id}"
DETAIL_URL_TEMPLATE = "{origin}/xq/detail/{xiaoqu_id}.html"
# 小区条目的 type 值（1=城区 2=片区 4=地铁 0=小区），只收 0。
COMMUNITY_TYPE = "0"

# 名称变体容错用：类型尾缀（乐有家会省略，如「飞扬时代大厦」→「飞扬时代」）
_TYPE_SUFFIXES = (
    "大厦", "公寓", "花园", "新村", "宿舍楼", "宿舍", "广场", "中心", "大楼",
    "综合楼", "住宅楼", "家园", "小区", "公馆", "别墅", "一期", "二期", "三期", "四期", "五期",
    "六期", "七期", "八期", "A区", "B区", "C区", "D区",
)
_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
              "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


# 主干词用：内嵌行政区字样（如「宝安34区」→「34区」）
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

    目的：提高联想接口召回。实测「怡景花园-景庭苑」原名搜不到，用主干词
    「怡景花园」能命中母小区页（同页映射），与 fang 侧的处置一致。
    返回值**不含原名自身**。
    """
    base = re.sub(r"\s+", "", str(value or ""))
    if not base:
        return []
    parts = [p for p in re.split(r"[-–—·、,，()（）/|;；]+", base) if len(p) >= 2]
    out: list[str] = list(parts[:2])          # 分隔符前的主名（怡景花园-景庭苑 → 怡景花园）
    for item in [base, *parts]:
        for suffix in _STEM_QUERY_SUFFIXES:
            if item.endswith(suffix) and len(item) - len(suffix) >= 2:
                out.append(item[: -len(suffix)])
        for token in _REGION_TOKENS:
            if token in item and len(item) > len(token) + 1:
                out.append(item.replace(token, ""))
    return [item for item in dict.fromkeys(out) if len(item) >= 2 and item != base]


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


def _cn_digits_to_arabic(value: str) -> str:
    """把汉字数字换成阿拉伯数字（「桃源居十四区」→「桃源居14区」）。

    「十四」这类先整体替换，避免被逐字拆成「104」。仅处理常见 1~10，
    更复杂的数字不做猜测——宁可匹配不上，也不错配。
    """
    text = value
    for cn, ar in (("十一", "11"), ("十二", "12"), ("十三", "13"), ("十四", "14"),
                   ("十五", "15"), ("十六", "16"), ("十七", "17"), ("十八", "18"),
                   ("十九", "19"), ("十", "10")):
        text = text.replace(cn, ar)
    for cn, ar in _CN_DIGITS.items():
        if cn != "十":
            text = text.replace(cn, ar)
    return text


def name_variants(value: str) -> set[str]:
    """名称的全部容错键：原样、汉字数字转写、以及各自的类型尾缀剥离版。"""
    base = name_key(value)
    if not base:
        return set()
    variants = {base, name_key(_cn_digits_to_arabic(base))}
    for candidate in set(variants):
        for suffix in _TYPE_SUFFIXES:
            if candidate.endswith(suffix) and len(candidate) - len(suffix) >= 2:
                variants.add(candidate[: -len(suffix)])
    return {v for v in variants if len(v) >= 2}


def _visible_text(fragment: str) -> str:
    """去掉接口返回的高亮标记（``<font color="red">…</font>``）。"""
    return re.sub(r"<[^>]+>", "", str(fragment or "")).strip()


@dataclass(frozen=True)
class SuggestEntry:
    """queryConditionAndContext 返回的单条小区联想项。"""

    community_id: str
    name: str
    area: str          # areaName：行政区
    place: str         # placeName：片区
    on_sale_count: Optional[int]
    alias: str = ""


def parse_suggest_entries(payload_text: str) -> list[dict]:
    """解析联想接口返回（纯函数）：只收小区条目；name 去掉高亮标记。

    判别依据（2026-10-08 实测真实响应）：**小区条目看外层 row 的 ``type`` 是否为 0，
    且 ``data`` 里有 ``id``**；筛选条件（城区/片区/地铁）的 ``data.type`` 是 1/2/4
    且**没有 id**。注意小区条目的 ``data`` 里**不存在 type 字段**——早期误按
    ``data.type == "0"`` 判断，会把全部小区条目滤掉。
    """
    try:
        payload = json.loads(payload_text or "")
    except (json.JSONDecodeError, TypeError):
        return []
    entries: list[dict] = []
    for row in (payload or {}).get("searchArray") or []:
        row_type = row.get("type")
        if row_type is not None and str(row_type) != COMMUNITY_TYPE:
            continue
        data = row.get("data") or {}
        community_id = str(data.get("id") or "").strip()
        if not community_id:
            continue
        count = data.get("sellCount")
        entries.append(
            {
                "community_id": community_id,
                "name": _visible_text(data.get("name")),
                "area": str(data.get("areaName") or ""),
                "place": str(data.get("placeName") or ""),
                "on_sale_count": int(count) if isinstance(count, (int, float)) else None,
                "alias": str(data.get("alias") or ""),
            }
        )
    return entries


async def fetch_suggest(keyword: str, *, origin: str, debug: bool = False) -> list[dict]:
    """匿名调用联想接口，返回小区候选列表（带限速与重试）。

    注意 ``Accept-Encoding`` **是必需头**：2026-10-08 逐组隔离实测，不带它一律 403
    （只带 UA/Referer/Accept/X-Requested-With/Origin 也照样 403），带上即 200。
    该站风控把它当判据，故此处必须显式声明并自行解压。
    """
    url = origin + SUGGEST_PATH
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": origin + "/esf/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not A(Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    body = urlencode({"keyword": keyword}).encode("utf-8")

    def _request() -> str:
        with urlopen(Request(url, headers=headers, data=body),
                     timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
            if "gzip" in (response.headers.get("Content-Encoding") or ""):
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "ignore")

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
        try:
            payload_text = await asyncio.to_thread(_request)
            entries = parse_suggest_entries(payload_text)
            if debug:
                log.info("[debug] keyword=%s 原始响应前 300 字：%s", keyword, payload_text[:300])
            return entries
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            log.warning(
                "联想接口第 %d/%d 次调用失败（keyword=%s）：%s",
                attempt, MAX_ATTEMPTS, keyword, exc,
            )
            await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"联想接口连续 {MAX_ATTEMPTS} 次调用失败：{last_error}")


def build_candidate_names(city: str, administrative_district: str, community_name: str) -> list[str]:
    """构造搜索候选名：给定名 → 主数据正式名/别名 → **主干词**兜底。

    主干词排在最后（精确度最低），只在前面都搜不到时才启用；命中会标记为
    ``*_stem``，便于事后审计哪些是跨粒度映射来的。
    """
    names = [community_name]
    try:
        records = resolve_communities(city, administrative_district, community_name)
    except Exception as exc:
        log.warning("查询小区主数据失败，仅使用给定名搜索：%s（%s）", community_name, exc)
        records = []
    if records and len(records) != 1:
        log.info("主数据未唯一命中（%d 条），仅使用给定名搜索：%s", len(records), community_name)
        records = []
    if records:
        for candidate in [records[0].name, *(records[0].aliases or ())]:
            normalized = re.sub(r"\s+", "", str(candidate or ""))
            if normalized and normalized not in names:
                names.append(normalized)
    # 主干词兜底（剥期数/后缀/行政区字样）
    for base in list(names):
        for stem in stem_variants(base):
            if stem not in names:
                names.append(stem)
    log.info("小区[%s]候选搜索名：%s", community_name, names)
    return names


def entry_aliases(entry: dict) -> set[str]:
    """取条目别名（``alias`` 字段可能含多个、且带高亮标记）。"""
    raw = _visible_text(entry.get("alias") or "")
    return {part for part in re.split(r"[\s,，、/|;；]+", raw) if part}


def entry_name_variants(entry: dict) -> set[str]:
    """条目的全部容错名称键：正式名 + 别名的各种变体。

    别名的意义：接口用 ``alias`` 承载搜索同义词——实测搜「山海津」返回的条目
    正式名是「龙瑞佳园」、别名才是「山海津」，故别名必须参与匹配。
    """
    variants = name_variants(entry.get("name") or "")
    for alias in entry_aliases(entry):
        variants |= name_variants(alias)
    return variants


def match_entries(entries: list[dict], keyword: str, administrative_district: str) -> list[dict]:
    """按行政区 + 名称（正式名/别名，含变体容错）筛出命中项。"""
    expected = normalize_district(administrative_district)
    keyword_variants = name_variants(keyword)
    hits: list[dict] = []
    for entry in entries:
        if normalize_district(entry["area"]) != expected:
            continue
        if keyword_variants & entry_name_variants(entry):
            hits.append(entry)
    return hits


async def resolve_community(
    *,
    city: str,
    administrative_district: str,
    community_name: str,
    origin: str,
    debug: bool = False,
) -> dict:
    """解析单个小区：候选名逐个走联想接口，返回平台无关的解析结果。

    返回字段：matched_search_name / platform_name / platform_district /
    platform_area / xiaoqu_id / match_via / on_sale_count。
    全部候选名失败抛 RuntimeError（附候选明细供人工裁决）。
    """
    names = build_candidate_names(city, administrative_district, community_name)
    # 主干词集合：用于把「跨粒度命中」标记成 *_stem，便于事后审计
    stem_set = {stem for base in names for stem in stem_variants(base)}
    candidates_seen: dict[str, str] = {}
    for name in names:
        entries = await fetch_suggest(name, origin=origin, debug=debug)
        hits = match_entries(entries, name, administrative_district)
        for entry in hits:
            on_sale = entry["on_sale_count"] if entry["on_sale_count"] is not None else "未知"
            candidates_seen.setdefault(
                entry["community_id"],
                f"{entry['name']}（{entry['area']}/{entry['place']}，在售{on_sale}）",
            )
        chosen: Optional[dict] = None
        match_via = ""
        # 优先级一：**正式名**归一化全等命中唯一 → 直接采纳。
        # 必须与"别名全等"分开：实测搜「上善梧桐苑」时，正主 48932 的正式名就是它，
        # 而 48094 只是把「上善梧桐苑」列为别名——若两档混在一起会凑成两个 id 而误判歧义。
        name_exact_hits = [e for e in hits if name_key(e["name"]) == name_key(name)]
        alias_exact_hits = [
            e for e in hits
            if name_key(e["name"]) != name_key(name)
            and any(name_key(a) == name_key(name) for a in entry_aliases(e))
        ]
        if name_exact_hits and len({e["community_id"] for e in name_exact_hits}) == 1:
            chosen = name_exact_hits[0]
            match_via = "name_exact"
        # 优先级二：别名全等命中唯一 → 采纳（如搜「山海津」返回的正式名是「龙瑞佳园」）。
        elif alias_exact_hits and len({e["community_id"] for e in alias_exact_hits}) == 1:
            chosen = alias_exact_hits[0]
            match_via = "alias_exact"
        # 优先级三：其余命中必须收敛到唯一 id，多个即歧义，留人工。
        elif hits and len({entry["community_id"] for entry in hits}) == 1:
            chosen = hits[0]
            match_via = "name_variant"
        if chosen is not None:
            if name in stem_set:
                match_via = f"{match_via}_stem"      # 主干词命中（跨粒度映射）
            log.info(
                "候选名[%s]唯一命中 xiaoqu_id=%s 平台名=%s（%s %s，%s）",
                name, chosen["community_id"], chosen["name"],
                chosen["area"], chosen["place"], match_via,
            )
            return {
                "matched_search_name": name,
                "platform_name": chosen["name"],
                "platform_district": chosen["area"] or "未识别",
                "platform_area": chosen["place"] or "未识别",
                "xiaoqu_id": chosen["community_id"],
                "match_via": match_via,
                "on_sale_count": chosen["on_sale_count"],
            }
        if hits:
            log.warning(
                "候选名[%s]命中 %d 个小区，拒绝自动选择：%s",
                name, len({entry["community_id"] for entry in hits}),
                sorted({entry["community_id"] for entry in hits}),
            )
    detail = "；".join(f"{cid}: {desc}" for cid, desc in list(candidates_seen.items())[:10])
    suffix = "（仅列前 10 条）" if len(candidates_seen) > 10 else ""
    raise RuntimeError(f"候选名 {names} 均未唯一命中目标小区；候选明细：{detail or '无'}{suffix}")


async def collect_community(
    *,
    origin: str,
    city: str,
    administrative_district: str,
    community_district: str,
    community_name: str,
    debug: bool,
) -> dict:
    """处理单个小区：解析出 xiaoqu_id 后按乐有家惯例派生入口 URL。

    社区页初始化**只落入口**（``listing_page_url``）；详情 URL 仅作输出辅助。
    成交不落（乐有家不对外展示成交）。
    """
    resolved = await resolve_community(
        city=city,
        administrative_district=administrative_district,
        community_name=community_name,
        origin=origin,
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
        "community_detail_url": DETAIL_URL_TEMPLATE.format(origin=origin, xiaoqu_id=xiaoqu_id),
        "listing_page_url": ENTRY_URL_TEMPLATE.format(origin=origin, xiaoqu_id=xiaoqu_id),
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
) -> list[dict]:
    """批量执行乐有家小区入口初始化（匿名联想接口版），单小区失败不中断。

    签名与统一入口 ``init_community_pages`` 一致：manual_login / manual_close 为
    兼容参数（接口版无浏览器与登录态，自动忽略）。返回逐小区结果摘要（含 success
    标记），字段与落库契约一致（落库只读 ``success`` 与 ``listing_page_url``）。
    """
    if manual_login:
        log.info("接口版无登录态，忽略 manual_login 参数")
    start_url = get_start_url("lyj", city)
    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{(parsed.hostname or '').lower()}"

    summaries: list[dict] = []
    consecutive_failures = 0
    for community_name in community_names:
        try:
            summary = await collect_community(
                origin=origin,
                city=city,
                administrative_district=administrative_district,
                community_district=community_district,
                community_name=community_name,
                debug=debug,
            )
            summary["success"] = True
            consecutive_failures = 0
            summaries.append(summary)
            print(f"\n[成功] {community_name}")
            print(f"城市：{summary['city']}")
            print(f"行政区：{summary['administrative_district']}")
            print(f"片区（辅助）：{summary['community_district'] or '未提供'}")
            print(f"命中搜索名：{summary['matched_search_name']}")
            print(f"平台小区名称：{summary['platform_name']}（{summary['match_via']}命中，"
                  f"在售{summary['on_sale_count'] if summary['on_sale_count'] is not None else '未知'}）")
            print(f"平台行政区：{summary['platform_district']}")
            print(f"平台片区：{summary['platform_area']}")
            print(f"xiaoqu_id：{summary['xiaoqu_id']}")
            print(f"community_detail_url（辅助，不落库）：{summary['community_detail_url']}")
            print(f"listing_page_url：{summary['listing_page_url']}")
        except Exception as exc:
            log.error("小区[%s]处理失败，继续下一个：%s", community_name, exc)
            summaries.append(
                {"community_name": community_name, "success": False, "error": str(exc)}
            )
            print(f"\n[失败] {community_name} → {exc}")
            # 风控停止闸：只对**传输层失败**（403 / 接口连续调用失败）累加，
            # 「候选名均未唯一命中」是正常业务结果（该小区平台上就没有），必须归零——
            # 否则一批里连着几个查不到的小区就会误判成被拦而中止整批。
            blocked_like = any(
                marker in str(exc)
                for marker in ("联想接口连续", "403", "Forbidden", "Timed out", "Timeout")
            )
            consecutive_failures = consecutive_failures + 1 if blocked_like else 0
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"\n[停止] 连续 {consecutive_failures} 次接口层失败，疑似被乐有家风控拦截，本批中止。\n"
                    f"       剩余小区**未被判定为失败**；稍后重跑本批即可（已落库的会自动跳过）。"
                )
                break

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
        description="乐有家小区挂牌页 URL 初始化 MVP（匿名联想接口版，无浏览器无登录）"
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
    parser.add_argument("--manual-login", action="store_true", help="兼容参数：接口版无需登录，忽略")
    parser.add_argument("--debug", action="store_true", help="打印联想接口原始响应片段")
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
