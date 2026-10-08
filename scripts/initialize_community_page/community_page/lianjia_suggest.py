# -*- coding: utf-8 -*-
"""链家系小区匿名联想解析核心（lj/ke 共用）。

链家 headerSearch 联想接口支持匿名调用（仅需 UA/Referer），返回候选小区的
communityId / title / region / 在售数，且 region 括号内携带别名。贝壳与链家
同属一个房源库、小区 communityId 完全一致（库内双平台小区实测 id 逐一相同），
因此 lj 与 ke 两个平台的入口初始化共用本模块完成“小区名→communityId”解析，
各平台 MVP 只负责按自己的域名派生入口 URL。

匹配规则（宁可缺不错配）：
- 候选名依次尝试：给定名 → 主数据正式名 → 其余别名（与浏览器版同一主数据来源）；
- 单个候选名的命中条件：region 行政区与主数据一致，且 title 归一化全等、
  候选名为 title 子串、或候选名出现在 region 别名括号内；
- 采纳优先级一：title 归一化全等的命中若唯一（communityId 唯一），直接采纳
  ——别名/子串撞车不影响精确命中；
- 采纳优先级二：否则要求全部命中收敛到唯一 communityId，多个即记歧义并换
  下一个候选名；全部候选名失败才判定该小区失败，错误信息附候选明细供人工裁决
  （桃源居综合楼、领航城三期这类需人工映射的条目会在此显式暴露）。

调用方：``lj_community_page_api_mvp``（lj 入口初始化）。
旧的浏览器版 MVP（lj/ke_community_page_mvp）已于 2026-09-30 删除，不留兜底。
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.community_data import resolve_communities

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
    """统一“福田”和“福田区”等行政区写法（与浏览器版实现一致）。"""
    text = re.sub(r"\s+", "", str(value or ""))
    for suffix in ("行政区", "新区", "区"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def name_key(value: str) -> str:
    """名称归一化键：仅保留字母/数字/汉字并转小写。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


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
    """查询小区主数据构造搜索候选名：给定名优先，正式名与其余别名兜底（与浏览器版一致）。"""
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
