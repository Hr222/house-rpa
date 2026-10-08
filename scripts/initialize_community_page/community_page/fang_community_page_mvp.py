# -*- coding: utf-8 -*-
"""房天下小区挂牌页 URL 初始化 MVP——匿名搜索联想 API 版（无浏览器、无登录）。

数据源：房天下二手房「搜索联想接口」
``/asynclist/searchsuggestion/suggestionList``（匿名可达，无需登录态）。

## 为什么是这条接口（2026-09-30 实测结论）

房天下对本机的风控是**路径级**，不是 IP 级黑名单：

| 路径 | 结果 |
|---|---|
| ``GET /`` 城市首页 | 200 正常 |
| ``GET /asynclist/searchsuggestion/suggestionList`` | 200 正常（本模块数据源） |
| ``GET /house/kw{词}/`` 搜索结果页 | 滑块验证码拦截页 |
| ``GET /house-xm{id}`` 挂牌页 | 滑块验证码拦截页 |
| ``GET /loupan/{id}/chengjiao/`` 成交页 | 滑块验证码拦截页 |

两版历史实现都栽在这里：

- 旧 HTTP 版（本模块替换掉的那版）爬的是被拦的 ``/house/kw`` 搜索页，
  且**没有拦截探测**——把验证码页当成“小区不存在”，2026-09-29 宝安一次性
  产出 461 条假失败（连续 8 个清单文件全 0 成功 / 50 失败）。
- 旧浏览器版（``fang_community_page_mvp``，已删除）虽然写了本接口作兜底，但
  ``parse_suggest_hits`` 用 ``json.loads`` 解析 ``fangcb({'hit':...})``
  ——响应是 **JS 对象字面量（key 带单引号）**，必然抛 JSONDecodeError，
  当日志里 431 次调用 0 次命中，兜底从未生效。

## 本模块链路

1. ``GET`` 城市首页预热 cookie；
2. ``GET /asynclist/searchsuggestion/suggestionList?jsoncallback=fangcb&city=&q=&purpose=``
   返回 ``fangcb({'hit':[{"id":"2810258062","projname":"泰华花园",
   "realname":...,"projaliasnames":"别名...","district":"宝安","comerce":"新安",
   "esfcount":"0"}]})``：
   - ``id`` 即挂牌入口 ``/house-xm{id}`` 的 ID，无需推断；
   - ``district`` 是平台已归一的行政区，另有别名 / 片区 / 在售套数；
   - 走 UTF-8 传参，天然规避旧搜索页路线的 GBK 编码限制
     （``德弘基•碧桂园•前海荟`` 这类带“·”的名字不再报错）。
3. 匹配（宁可缺不错配）：行政区归一化相等 + 候选名与 projname / realname / 别名
   命中；title 归一化全等唯一时优先采纳；否则要求命中收敛到唯一 id。
   全部候选名失败 → ``success=False`` 附候选明细留人工。
4. **契约失效探测 + 人工救援**：响应不是合法 ``fangcb(...)`` 载荷（典型为滑块验证码
   页）即判定接口失效，抛 ``FangSuggestBlockedError``，绝不静默返回空、绝不把拦截
   记成“小区不存在”（合法空结果 ``fangcb({'hit':[]})`` 不算失效）。被拦时**弹出
   可见浏览器交人工完成滑块验证 → 取回浏览器 cookie 回灌 → 重试当前小区**继续
   本批（``manual_rescue``）；只有连续 ``MAX_MANUAL_RESCUES`` 次人工介入仍被拦、
   或人工选择停止，才中止本批，且中止时**不把剩余小区记为失败**。

边界：只拿入口 URL，不爬房源数据。因此不做实开挂牌页复核（该路径同样被拦），
归属信心由“行政区核对 + 名称/别名精确匹配 + id 唯一”提供。

用法（独立运行，签名与统一入口一致）：
  python -m scripts.initialize_community_page.community_page.fang_community_page_mvp \
      --city "深圳" --administrative-district "宝安区" --community "泰华花园" "桃源居"
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gzip
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
from app.rpa.platforms.base import community_name_match
from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.logging_utils import setup_logging

setup_logging()
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 联想接口是轻量 JSON 接口，实测 0.4s 间隔连打 12 次无限流（样本有限，
# 触发限流时下调到 1.0~2.5s 即可）。
REQUEST_INTERVAL_SECONDS = 0.4
REQUEST_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# 联想接口的 purpose 白名单（沿用浏览器版实测可用的取值）。
SUGGEST_PURPOSE = "住宅,别墅,商业,用户词,社区"

# 风控拦截页特征（滑块验证码）：命中即接口契约失效，弹浏览器交人工过验证后回灌 cookie 继续。
BLOCK_MARKERS = ("checkyzm", "请完成下列验证后继续", "/fangue/libs/check/")

# 单批最多允许几次“人工过验证”——防止在人工也无法通过时无限弹窗。
MAX_MANUAL_RESCUES = 3

# fangcb({...}); ——回调名不固定，统一按“标识符(载荷);”剥离。
_JSONP_CALL = re.compile(r"^\s*[A-Za-z_$][\w$]*\s*\(\s*(?P<payload>.*?)\s*\)\s*;?\s*$", re.S)


class FangSuggestBlockedError(RuntimeError):
    """房天下联想接口契约失效（典型为滑块验证码风控），需人工介入后重跑本批。"""


@dataclass(frozen=True)
class SuggestEntry:
    """联想接口返回的单条楼盘候选。"""

    community_id: str
    projname: str
    realname: str
    aliases: tuple[str, ...]
    district: str
    area: str
    on_sale_count: Optional[int]

    def names(self) -> tuple[str, ...]:
        """楼盘正式名 / 别名 / 简称的合并候选名集合。"""
        collected = [self.projname, self.realname, *self.aliases]
        return tuple(name for name in dict.fromkeys(n for n in collected if n))


def normalize_district(value: str) -> str:
    """统一“福田”和“福田区”等行政区写法（与 lj/lyj 实现一致）。"""
    text = re.sub(r"\s+", "", str(value or ""))
    for suffix in ("行政区", "新区", "区"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def name_key(value: str) -> str:
    """名称归一化键：仅保留字母/数字/汉字并转小写。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


def _split_aliases(value: object) -> tuple[str, ...]:
    """拆分 ``projaliasnames``（实测空格分隔，另兼容中英文逗号/顿号）。"""
    return tuple(
        part.strip()
        for part in re.split(r"[\s,，、;；|]+", str(value or ""))
        if part.strip()
    )


def _to_int(value: object) -> Optional[int]:
    """把接口返回的字符串计数转成 int；无法解析返回 None。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _resolve_payload(text: str) -> dict:
    """剥离 JSONP 壳并解析为 dict；形如验证码页等非法载荷抛 FangSuggestBlockedError。

    实测响应是 **JS 对象字面量**（key 带单引号，且同一载荷里混用双引号，例如
    ``fangcb({'hit':[],"history":[]})``），不能直接用 ``json.loads``。这里先按
    标准 JSON 解析，失败再把单引号归一后重试。
    """
    raw = (text or "").strip()
    if not raw:
        raise FangSuggestBlockedError("联想接口返回空响应")
    if any(marker in raw for marker in BLOCK_MARKERS):
        raise FangSuggestBlockedError("联想接口返回滑块验证码页（风控拦截）")
    call = _JSONP_CALL.match(raw)
    if call is None:
        raise FangSuggestBlockedError(f"联想接口返回非 JSONP 载荷：{raw[:200]!r}")
    payload_text = call.group("payload")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        normalized = re.sub(r"([{,]\s*)'([^']*)'(\s*:)", r'\1"\2"\3', payload_text)
        normalized = re.sub(r"(:\s*)'([^']*)'", r'\1"\2"', normalized)
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise FangSuggestBlockedError(
                f"联想接口载荷无法解析（疑似契约变更）：{payload_text[:200]!r}"
            ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("hit"), list):
        raise FangSuggestBlockedError(
            f"联想接口载荷缺少 hit 数组（疑似契约变更）：{str(payload)[:200]}"
        )
    return payload


def parse_suggest_payload(text: str) -> list[SuggestEntry]:
    """解析联想接口响应，返回楼盘类候选（纯函数，便于单测）。

    只保留 ``wordtype == "楼盘"`` 且带数字 id 的条目（其余为小区/地铁等噪声）。
    合法空结果返回空列表；载荷非法抛 FangSuggestBlockedError。
    """
    payload = _resolve_payload(text)
    entries: list[SuggestEntry] = []
    for row in payload["hit"]:
        if not isinstance(row, dict) or row.get("wordtype") != "楼盘":
            continue
        community_id = str(row.get("id") or "").strip()
        if not community_id.isdigit():
            continue
        entries.append(
            SuggestEntry(
                community_id=community_id,
                projname=str(row.get("projname") or "").strip(),
                realname=str(row.get("realname") or "").strip(),
                aliases=_split_aliases(row.get("projaliasnames")),
                district=str(row.get("district") or "").strip(),
                area=str(row.get("comerce") or "").strip(),
                on_sale_count=_to_int(row.get("esfcount")),
            )
        )
    return entries


class _FangHttpClient:
    """匿名 HTTP 客户端（仅走搜索联想接口），cookie 以字典显式持有。

    cookie 不交给 CookieJar 而是显式持有，是为了支持**风控救援**：命中滑块
    验证码时弹出浏览器交人工过验证，再把浏览器 cookie 原样回灌进来继续跑。
    """

    def __init__(self, origin: str, cookies: Optional[dict[str, str]] = None) -> None:
        self.origin = origin
        self._cookies: dict[str, str] = dict(cookies or {})
        self._warmed = False

    def _fetch(self, url: str) -> str:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": self.origin + "/",
            "Accept-Encoding": "gzip",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        request = Request(url, headers=headers)
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            self._absorb_cookies(response.headers.get_all("Set-Cookie") or [])
            raw = response.read()
            if "gzip" in (response.headers.get("Content-Encoding") or ""):
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "ignore")

    def _absorb_cookies(self, set_cookie_headers: list) -> None:
        """把响应 Set-Cookie 合并进会话（只取 name=value，丢弃属性段）。"""
        for header in set_cookie_headers:
            pair = str(header).split(";", 1)[0].strip()
            name, sep, value = pair.partition("=")
            if sep and name.strip():
                self._cookies[name.strip()] = value.strip()

    def inject_cookies(self, cookies: dict[str, str]) -> None:
        """回灌人工过验证后取得的浏览器 cookie，并强制重新预热会话。"""
        incoming = {k.strip(): v for k, v in (cookies or {}).items() if k and k.strip()}
        if not incoming:
            log.warning("回灌 cookie 为空，会话未变更")
            return
        self._cookies.update(incoming)
        self._warmed = False
        log.info("已回灌 %d 个 cookie（累计 %d 个），会话将重新预热", len(incoming), len(self._cookies))

    async def warm_up(self) -> None:
        """GET 城市首页预热 cookie（连续失败抛 RuntimeError）。"""
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await asyncio.to_thread(self._fetch, self.origin + "/")
                self._warmed = True
                return
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                log.warning("房天下首页预热第 %d/%d 次失败：%s", attempt, MAX_ATTEMPTS, exc)
                await asyncio.sleep(2 * attempt)
        raise RuntimeError(f"房天下首页连续 {MAX_ATTEMPTS} 次预热失败：{last_error}")

    def _suggest_url(self, keyword: str, city: str) -> str:
        return (
            f"{self.origin}/asynclist/searchsuggestion/suggestionList"
            f"?jsoncallback=fangcb&city={quote(city, safe='')}"
            f"&q={quote(keyword, safe='')}"
            f"&purpose={quote(SUGGEST_PURPOSE, safe='')}"
        )

    async def suggest(self, keyword: str, *, city: str) -> list[SuggestEntry]:
        """按关键词调用联想接口（带限速与重试）。

        网络层连续失败抛 RuntimeError；载荷非法（风控/契约变更）抛
        FangSuggestBlockedError 且**不重试**——拦截下重试没有意义，
        只会把问题拖成静默失败。
        """
        if not self._warmed:
            await self.warm_up()
        url = self._suggest_url(keyword, city)
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
            try:
                text = await asyncio.to_thread(self._fetch, url)
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                log.warning(
                    "联想接口第 %d/%d 次调用失败（keyword=%s）：%s",
                    attempt, MAX_ATTEMPTS, keyword, exc,
                )
                await asyncio.sleep(2 * attempt)
                continue
            # 解析异常直接向上抛：契约失效不重试、不吞。
            return parse_suggest_payload(text)
        raise RuntimeError(f"联想接口连续 {MAX_ATTEMPTS} 次调用失败：{last_error}")


async def _browser_cookies(browser) -> dict[str, str]:
    """读取浏览器当前会话的全部 cookie（name→value）。"""
    cookies: dict[str, str] = {}
    try:
        items = await browser.cookies.get_all()
    except Exception as exc:
        log.warning("读取浏览器 cookie 失败：%s", exc)
        return cookies
    for item in items or []:
        name = getattr(item, "name", None)
        value = getattr(item, "value", None)
        if name and value is not None:
            cookies[str(name)] = str(value)
    return cookies


async def manual_rescue(*, origin: str, round_index: int) -> dict[str, str]:
    """风控拦截时弹出**可见浏览器**交人工完成滑块验证，取回 cookie 供回灌。

    关键点：浏览器必须使用与 HTTP 客户端**完全一致的 User-Agent**——房天下的
    放行 cookie 通常与 IP + UA 绑定，UA 不一致会让回灌失效。

    人工确认门（延续工程既有手动门惯例）：
    - 回车 → 已通过验证，继续本批；
    - 输入 n / q / 停 → 放弃本批（抛 FangSuggestBlockedError，已得结果保留，
      剩余小区**不记为失败**）。
    """
    import nodriver as uc

    from app.rpa.core import config

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
        browser_args=[f"--user-agent={USER_AGENT}"],
    )
    try:
        page = await browser.get(origin + "/")
        await page
        await asyncio.sleep(3)
        try:
            answer = await asyncio.to_thread(
                input,
                f"\n[人工介入 #{round_index}] 房天下要求滑块验证，已弹出浏览器。\n"
                f"  1) 在浏览器里完成滑块验证（不必再点搜索，过了就行）\n"
                f"  2) 回终端按【回车】= 已通过，继续本批\n"
                f"     输入 n 后回车 = 停止本批（已得结果保留，剩余不记为失败）\n"
                f"> ",
            )
        except EOFError as exc:
            # 无人值守（无交互终端）时拿不到人工确认：明确交人工，不假装通过。
            raise FangSuggestBlockedError("无交互终端，无法获得人工验证确认") from exc
        if str(answer).strip().lower() in {"n", "no", "q", "quit", "stop", "停"}:
            raise FangSuggestBlockedError("人工选择停止本批")
        cookies = await _browser_cookies(browser)
        if not cookies:
            log.warning("未从浏览器取到 cookie，回灌可能无效")
        return cookies
    finally:
        try:
            browser.stop()
        except Exception:
            pass


def build_candidate_names(city: str, administrative_district: str, community_name: str) -> list[str]:
    """查询小区主数据构造搜索候选名：给定名优先，正式名与其余别名兜底（与 lj/lyj 一致）。"""
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


def _match_entries(
    entries: list[SuggestEntry], keyword: str, administrative_district: str
) -> list[SuggestEntry]:
    """按行政区 + 名称/别名规则筛出命中项。

    行政区归一化后严格相等；名称命中 = 归一化全等、候选名是楼盘名子串，
    或工程件 community_name_match 判定一致（带期数保护，避免一期匹配到二期）；
    别名命中 = 候选名出现在 projaliasnames 里。
    """
    expected = normalize_district(administrative_district)
    hits: list[SuggestEntry] = []
    for entry in entries:
        if normalize_district(entry.district) != expected:
            continue
        name_hit = (
            name_key(entry.projname) == name_key(keyword)
            or keyword in entry.projname
            or community_name_match(keyword, entry.projname)
        )
        alias_hit = any(
            alias == keyword or name_key(alias) == name_key(keyword)
            for alias in entry.aliases
        )
        if name_hit or alias_hit:
            hits.append(entry)
    return hits


async def resolve_community(
    *,
    client: _FangHttpClient,
    city: str,
    administrative_district: str,
    community_name: str,
) -> dict:
    """解析单个小区：候选名逐个走联想接口，返回平台无关的解析结果。

    返回字段：matched_search_name / platform_name / platform_district /
    platform_area / xiaoqu_id / match_via / on_sale_count。
    全部候选名失败抛 RuntimeError（附候选明细供人工裁决）；
    接口契约失效抛 FangSuggestBlockedError（由 main 停止本批）。
    """
    names = build_candidate_names(city, administrative_district, community_name)
    candidates_seen: dict[str, str] = {}
    for name in names:
        entries = await client.suggest(name, city=city)
        hits = _match_entries(entries, name, administrative_district)
        for entry in hits:
            on_sale = entry.on_sale_count if entry.on_sale_count is not None else "未知"
            candidates_seen.setdefault(
                entry.community_id,
                f"{entry.projname}（{entry.district}/{entry.area}，在售{on_sale}）",
            )
        chosen: Optional[SuggestEntry] = None
        match_via = ""
        # 优先级一：楼盘名归一化全等命中唯一时直接采纳，别名/子串撞车不否决精确命中。
        exact_hits = [entry for entry in hits if name_key(entry.projname) == name_key(name)]
        if exact_hits and len({entry.community_id for entry in exact_hits}) == 1:
            chosen = exact_hits[0]
            match_via = "projname_exact"
        # 优先级二：其余命中必须收敛到唯一 id，多个即歧义，留人工。
        elif hits and len({entry.community_id for entry in hits}) == 1:
            chosen = hits[0]
            match_via = "alias" if chosen.projname != name else "projname"
        if chosen is not None:
            log.info(
                "候选名[%s]唯一命中 xiaoqu_id=%s 平台名=%s（%s %s，%s）",
                name, chosen.community_id, chosen.projname,
                chosen.district, chosen.area, match_via,
            )
            return {
                "matched_search_name": name,
                "platform_name": chosen.projname,
                "platform_district": chosen.district or "未识别",
                "platform_area": chosen.area or "未识别",
                "xiaoqu_id": chosen.community_id,
                "match_via": match_via,
                "on_sale_count": chosen.on_sale_count,
            }
        if hits:
            log.warning(
                "候选名[%s]命中 %d 个楼盘，拒绝自动选择：%s",
                name, len({entry.community_id for entry in hits}),
                sorted({entry.community_id for entry in hits}),
            )
    detail = "；".join(f"{cid}: {desc}" for cid, desc in list(candidates_seen.items())[:10])
    suffix = "（仅列前 10 条）" if len(candidates_seen) > 10 else ""
    raise RuntimeError(f"候选名 {names} 均未唯一命中目标小区；候选明细：{detail or '无'}{suffix}")


def build_summary(
    *,
    city: str,
    administrative_district: str,
    community_district: str,
    community_name: str,
    origin: str,
    resolved: dict,
) -> dict:
    """由解析结果构造统一入口约定的逐小区结果摘要（主链路与补漏通道共用）。"""
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
        "community_detail_url": f"{origin}/house-xm{xiaoqu_id}",
        "listing_page_url": f"{origin}/house-xm{xiaoqu_id}",
        "match_via": resolved["match_via"],
        "on_sale_count": resolved["on_sale_count"],
    }


async def collect_community(
    *,
    client: _FangHttpClient,
    origin: str,
    city: str,
    administrative_district: str,
    community_district: str,
    community_name: str,
) -> dict:
    """处理单个小区：解析出 xiaoqu_id 后按房天下列盘同 id 惯例派生入口 URL。"""
    resolved = await resolve_community(
        client=client,
        city=city,
        administrative_district=administrative_district,
        community_name=community_name,
    )
    return build_summary(
        city=city,
        administrative_district=administrative_district,
        community_district=community_district,
        community_name=community_name,
        origin=origin,
        resolved=resolved,
    )


# ───────────────────── 可选：搜索引擎补漏通道（默认关闭）─────────────────────
# 定位：主链路是**纯 HTTP**（匿名联想接口），本节是 `--recover` 才启用的兜底。
# 为什么要它：房天下对"无二手房"的小区在联想接口里召回很差。2026-09-30 实测
# 宝安 615 个清单小区中 209 个接口搜不到，其中相当一部分其实有楼盘/小区页。
# 做法：真浏览器搜 Bing「{小区名} 房天下」→ 只认 sz 域名的房天下 id →
# 抓手机站小区页逐条核对（名称 + 行政区）→ 通过才采纳。
# 为何用浏览器：HTTP 直连搜索引擎会被反爬（百度返回安全验证、Bing 走海外版无结果）。

RECOVER_ENGINE_TEMPLATES = ("{n} 房天下", "{n} 小区 房天下", "{n} 深圳 房天下")
RECOVER_ENGINE_ORIGIN = "https://cn.bing.com/search?q="
RECOVER_INTERVAL_SECONDS = 1.2
RECOVER_MAX_CANDIDATES = 3
RECOVER_MOBILE_VERIFY_URL = "https://m.fang.com/xiaoqu/sz-{fid}.html"
_SZ_HOST = re.compile(r"https?://(?:sz\.(?:esf|newhouse)\.fang\.com|m\.fang\.com/xiaoqu/sz)")
_FANG_ID = re.compile(r"(?:loupan|house-xm)[/-]?(\d{7,})|xiaoqu/sz-(\d{7,})")
# 房天下给每个住宅小区还挂着「商铺 / 写字楼 / 办公」等**兄弟实体**，名字互相包含。
# 只按名称匹配会把住宅错配到商铺页（实测踩过：名豪丽城 → 名豪丽城商铺），
# 故这些后缀一旦出现在房天下名里、而目标名自己不含该词，即否决。
_SIBLING_SUFFIXES = ("商铺", "写字楼", "办公", "商住楼", "厂房", "仓库")
# 期数/分区/类型尾缀：剥掉后得到主干词，用于跨粒度匹配
# （如「龙珠山顶花园宝安区」↔「龙珠山顶花园」、「莲花山庄芙蓉苑」↔「莲花山庄」）。
_STRIP_SUFFIXES = (
    "一期", "二期", "三期", "四期", "五期", "六期", "七期", "八期",
    "A区", "B区", "C区", "D区", "北区", "南区", "东区", "西区", "北小区", "南小区",
    "大厦", "公寓", "花园", "新村", "宿舍楼", "宿舍", "广场", "中心", "大楼",
    "综合楼", "住宅楼", "家园", "小区",
)
_DISTRICT_TOKENS = ("宝安", "龙岗", "南山", "罗湖", "福田", "龙华", "盐田", "光明", "坪山", "大鹏")


# 补漏**查询词**专用：激进后缀（含单字），只为扩大搜索引擎召回，**不参与核对**。
# 2026-10-08 实测：只用清单原名搜 Bing 命中 ~12%，放进单字后缀（苑/府/园/大院）
# 后升到 ~50%（旭飞华天苑→旭飞华天、幸福华府→幸福华、航运大院→航运）。
_QUERY_STRIP_SUFFIXES = (
    "山庄", "大院", "别墅", "大厦", "公寓", "花园", "新村", "宿舍楼", "宿舍",
    "广场", "中心", "大楼", "综合楼", "住宅楼", "家园", "小区", "公馆",
    "苑", "阁", "庭", "轩", "居", "邨", "村", "城", "园", "府", "坊", "里",
    "台", "湾", "岛", "岸",
    "一期", "二期", "三期", "四期", "五期", "六期", "七期", "八期",
    "A区", "B区", "C区", "D区",
)
RECOVER_MAX_QUERY_WORDS = 3      # 清单名 + 最多 2 个主干词，控制请求量


def _recover_query_words(names: list[str]) -> list[str]:
    """补漏的候选查询词：清单名优先，其后是主干词（剥后缀/按分隔符切主名/去行政区字样）。

    只影响「拿什么词去搜」，核对仍走 _recover_stems + 行政区 + 页面标题，不放松。
    """
    words: list[str] = []
    for base in names[:1]:
        cleaned = re.sub(r"\s+", "", str(base or ""))
        if cleaned:
            words.append(cleaned)
    for base in names:
        cleaned = re.sub(r"\s+", "", str(base or ""))
        if not cleaned:
            continue
        parts = [item for item in re.split(r"[-–—·、,，()（）/|;；]+", cleaned) if len(item) >= 2]
        for item in [cleaned, *parts]:
            for suffix in _QUERY_STRIP_SUFFIXES:
                if item.endswith(suffix) and len(item) - len(suffix) >= 2:
                    words.append(item[: -len(suffix)])
            for token in _DISTRICT_TOKENS:
                if token in item and len(item) > len(token) + 1:
                    words.append(item.replace(token, ""))
    return [word for word in dict.fromkeys(words) if len(word) >= 2][:RECOVER_MAX_QUERY_WORDS]


def _recover_stems(names: list[str]) -> list[str]:
    """由候选名（清单名 + 主数据正式名 + 别名）派生主干词集合。"""
    stems: set[str] = set()
    for base in names:
        base = re.sub(r"\s+", "", str(base or ""))
        if not base:
            continue
        stems.add(base)
        for suffix in _STRIP_SUFFIXES:
            if base.endswith(suffix) and len(base) - len(suffix) >= 2:
                stems.add(base[: -len(suffix)])
    return sorted(stem for stem in stems if len(stem) >= 3)


def _recover_name_hit(fang_name: str, stems: list[str]) -> bool:
    """主干词与房天下名**双向包含**才算命中。

    双向是必须的：只要求"主干词落在房天下名里"（单向）会误杀
    「龙珠山顶花园宝安区 → 龙珠山顶花园」这类"清单名更长"的正常粒度差异；
    双向同时能把「田贝三路 → 燃气小区」这种靠页面正文街道名蹭上的误配挡掉。
    """
    target = name_key(fang_name)
    if not target:
        return False
    return any((name_key(stem) in target) or (target in name_key(stem)) for stem in stems)


def _recover_is_sibling(fang_name: str, community_name: str) -> bool:
    """房天下名带商铺/写字楼等后缀、而目标名自己不带 → 判为兄弟实体。"""
    return any(suffix in fang_name and suffix not in community_name
               for suffix in _SIBLING_SUFFIXES)


def _extract_sz_ids(html_text: str) -> dict[str, str]:
    """从搜索结果页抠出**深圳**房天下的 id；跨城同名（珠海 zh / 天津 tj …）一律丢弃。"""
    found: dict[str, str] = {}
    for match in re.finditer(r"https?://[^\s\"'<>()&]+", html_text or ""):
        url = match.group(0)
        if not _SZ_HOST.search(url):
            continue
        id_match = _FANG_ID.search(url)
        if id_match:
            found.setdefault(id_match.group(1) or id_match.group(2), url[:100])
    return found


def _recover_mobile_html(xiaoqu_id: str) -> str:
    """抓手机站小区页（无需登录态与 cookie，比 PC 内容页稳定）。"""
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
               "Referer": "https://m.fang.com/xiaoqu/sz/"}
    with urlopen(Request(RECOVER_MOBILE_VERIFY_URL.format(fid=xiaoqu_id), headers=headers),
                 timeout=REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read()
        if "gzip" in (response.headers.get("Content-Encoding") or ""):
            raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "ignore")
    return text if text.count("\ufffd") < 30 else raw.decode("gb18030", "ignore")


def verify_recover_candidate(
    *,
    xiaoqu_id: str,
    community_name: str,
    names: list[str],
    administrative_district: str,
) -> Optional[dict]:
    """抓手机站小区页核对候选，通过返回 {"fang_name", "title"}，否则 None。

    行政区**只在页面里出现且冲突时否决**：手机站页面前 6000 字常常不出现行政区
    （实测「华府国际」页面标题正确但正文无"南山"），硬要求会把正确候选误杀。
    """
    try:
        html_text = _recover_mobile_html(xiaoqu_id)
    except (URLError, OSError) as exc:
        log.warning("补漏核对 id=%s 页面抓取失败：%s", xiaoqu_id, exc)
        return None
    title_match = re.search(r"<title>(.*?)</title>", html_text, re.S)
    title = title_match.group(1).strip() if title_match else ""
    fang_name = title.split("-")[0].strip() if title else ""
    if not fang_name:
        return None
    if _recover_is_sibling(fang_name, community_name):
        log.info("补漏候选 id=%s 判为商铺/写字楼兄弟实体（%s），否决", xiaoqu_id, fang_name)
        return None
    observed = [token for token in _DISTRICT_TOKENS if token in html_text[:6000]]
    expected = normalize_district(administrative_district)
    if observed and observed != [expected]:
        log.info("补漏候选 id=%s 行政区冲突（页面=%s，期望=%s），否决",
                 xiaoqu_id, observed, expected)
        return None
    if not _recover_name_hit(fang_name, _recover_stems(names)):
        log.info("补漏候选 id=%s 名称不符（房天下名=%s），否决", xiaoqu_id, fang_name)
        return None
    return {"fang_name": fang_name, "title": title}


async def _recover_search_ids(
    community_name: str,
    templates: tuple[str, ...] = RECOVER_ENGINE_TEMPLATES,
) -> dict[str, str]:
    """真浏览器搜 Bing，返回 {fang_id: 来源url}；多个查询词按序尝试，命中即停。"""
    import nodriver as uc

    from app.rpa.core import config

    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )
    try:
        found: dict[str, str] = {}
        for template in templates:
            query = template.format(n=community_name)
            page = await browser.get(RECOVER_ENGINE_ORIGIN + quote(query, safe=""))
            await page
            await asyncio.sleep(RECOVER_INTERVAL_SECONDS)
            try:
                html_text = await page.evaluate("document.documentElement.outerHTML") or ""
            except Exception as exc:
                log.warning("补漏搜索[%s]读取页面失败：%s", query, exc)
                continue
            found.update(_extract_sz_ids(html_text))
            if found:
                break
        return found
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def recover_by_search(
    *,
    city: str,
    administrative_district: str,
    community_name: str,
) -> Optional[dict]:
    """补漏：搜索引擎找 id → 手机站逐条核对。返回与 resolve_community 同构的结果。"""
    names = build_candidate_names(city, administrative_district, community_name)
    # 清单名先试全部问法；主干词只试首个问法（控制请求量）。
    found: dict[str, str] = {}
    for index, word in enumerate(_recover_query_words(names)):
        templates = RECOVER_ENGINE_TEMPLATES if index == 0 else RECOVER_ENGINE_TEMPLATES[:1]
        try:
            found = await _recover_search_ids(word, templates)
        except Exception as exc:
            log.warning("补漏搜索[%s]异常：%s", word, exc)
            found = {}
        if found:
            log.info("补漏：小区[%s] 用查询词「%s」搜到 %d 个候选", community_name, word, len(found))
            break
    if not found:
        log.info("补漏：小区[%s] 搜索引擎无深圳房天下结果", community_name)
        return None
    for xiaoqu_id in sorted(found)[:RECOVER_MAX_CANDIDATES]:
        verified = verify_recover_candidate(
            xiaoqu_id=xiaoqu_id,
            community_name=community_name,
            names=names,
            administrative_district=administrative_district,
        )
        if verified:
            log.info("补漏命中：小区[%s] → xiaoqu_id=%s（%s）",
                     community_name, xiaoqu_id, verified["fang_name"])
            return {
                "matched_search_name": names[0],
                "platform_name": verified["fang_name"],
                "platform_district": administrative_district,
                "platform_area": "未识别",
                "xiaoqu_id": xiaoqu_id,
                "match_via": "search_recover",
                "on_sale_count": None,
            }
    log.info("补漏：小区[%s] 搜索到 %d 个候选，均未通过页面核对", community_name, len(found))
    return None


async def main(
    city: str,
    administrative_district: str,
    community_names: list[str],
    community_district: str = "",
    manual_login: bool = False,
    debug: bool = False,
    manual_close: bool = True,
    include_deal: bool = False,
    recover: bool = False,
) -> list[dict]:
    """批量执行房天下小区入口初始化（匿名联想 API 版），单小区失败不中断。

    签名与统一入口 ``init_community_pages`` 一致：manual_login / manual_close 为
    兼容参数（API 模式无浏览器与登录态，自动忽略）；include_deal=True 时按
    “挂牌页与楼盘页同 id”惯例派生成交页 URL（``/loupan/{id}/chengjiao/``，
    与库内既有格式一致，不实开复核）。

    **风控拦截走人工救援，不静默、不记失败**：命中 FangSuggestBlockedError 时
    弹出可见浏览器交人工完成滑块验证，取回 cookie 回灌后**重试当前小区**继续
    本批（见 ``manual_rescue``）。只有连续 ``MAX_MANUAL_RESCUES`` 次人工介入后
    仍被拦、或人工选择停止，才中止本批——此时未处理的小区**不写入任何“失败”
    记录**，避免把拦截污染成“房天下没有这个小区”。任一小区成功后救援预算重置。

    返回逐小区结果摘要（含 success 标记），字段与统一入口落库契约一致。

    ``recover=True`` 时启用**可选补漏通道**（默认关闭）：主链路（联想接口）未命中
    才用真浏览器搜 Bing 找 id，再抓手机站小区页核对（见模块内"搜索引擎补漏通道"节）。
    该通道会弹出浏览器窗口，故默认关闭；主链路本身仍是纯 HTTP。
    """
    if manual_login:
        log.info("API 模式无登录态，忽略 manual_login 参数（风控拦截默认触发人工救援）")
    start_url = get_start_url("fang", city)
    parsed_url = urlparse(start_url)
    origin = f"{parsed_url.scheme}://{(parsed_url.hostname or '').lower()}"

    summaries: list[dict] = []
    client: Optional[_FangHttpClient] = None
    if debug:
        _dump_dir = PROJECT_ROOT / "results" / "community_page" / "fang_api_debug"
        _dump_dir.mkdir(parents=True, exist_ok=True)
        log.info("--debug 已启用，联想接口原始响应目录：%s", _dump_dir)

    index = 0
    rescues_since_progress = 0
    while index < len(community_names):
        community_name = community_names[index]
        try:
            if client is None:
                client = _FangHttpClient(origin)
                await client.warm_up()
            summary = await collect_community(
                client=client,
                origin=origin,
                city=city,
                administrative_district=administrative_district,
                community_district=community_district,
                community_name=community_name,
            )
        except FangSuggestBlockedError as exc:
            remaining = len(community_names) - index
            if rescues_since_progress >= MAX_MANUAL_RESCUES:
                log.error(
                    "人工介入 %d 次后仍被拦截，停止本批：%s（已得 %d 条结果，剩余 %d 个小区未处理）",
                    rescues_since_progress, exc, len(summaries), remaining,
                )
                print(
                    f"\n[停止] 人工介入 {rescues_since_progress} 次后房天下仍拦截，本批中止。\n"
                    f"       已得 {len(summaries)} 条结果，剩余 {remaining} 个小区未处理，"
                    f"**这些小区未被判定为失败**。\n"
                    f"       稍后重跑本批即可（已落库的小区会自动跳过）。"
                )
                break
            rescues_since_progress += 1
            log.warning(
                "命中房天下风控，第 %d/%d 次抬起人工介入：%s",
                rescues_since_progress, MAX_MANUAL_RESCUES, exc,
            )
            try:
                cookies = await manual_rescue(origin=origin, round_index=rescues_since_progress)
            except FangSuggestBlockedError as stop:
                log.error(
                    "人工介入终止本批：%s（已得 %d 条结果，剩余 %d 个小区未处理）",
                    stop, len(summaries), remaining,
                )
                print(
                    f"\n[停止] {stop}。已得 {len(summaries)} 条结果，"
                    f"剩余 {remaining} 个小区未处理，**未被判定为失败**。"
                )
                break
            if client is None:
                client = _FangHttpClient(origin)
            client.inject_cookies(cookies)
            continue  # 不推进 index：用回灌后的会话重试当前小区
        except Exception as exc:
            # 可选补漏：主链路（联想接口）没命中时才启用真浏览器搜索兜底。
            # 补漏自身异常不影响主链路判定，仍按原样记为失败。
            recovered: Optional[dict] = None
            if recover:
                try:
                    recovered = await recover_by_search(
                        city=city,
                        administrative_district=administrative_district,
                        community_name=community_name,
                    )
                except Exception as recover_exc:
                    log.warning("小区[%s] 补漏通道异常（不影响失败判定）：%s",
                                community_name, recover_exc)
            if recovered is not None:
                summary = build_summary(
                    city=city,
                    administrative_district=administrative_district,
                    community_district=community_district,
                    community_name=community_name,
                    origin=origin,
                    resolved=recovered,
                )
                summary["success"] = True
                rescues_since_progress = 0
                if include_deal:
                    summary["deal_page_url"] = (
                        f"{origin}/loupan/{summary['xiaoqu_id']}/chengjiao/"
                    )
                summaries.append(summary)
                index += 1
                print(f"\n[补漏成功] {community_name} → xiaoqu_id={summary['xiaoqu_id']}"
                      f"（{summary['platform_name']}，search_recover 命中）")
                continue
            log.error("小区[%s]处理失败，继续下一个：%s", community_name, exc)
            summaries.append(
                {"community_name": community_name, "success": False, "error": str(exc)}
            )
            print(f"\n[失败] {community_name} → {exc}")
            index += 1
            continue

        summary["success"] = True
        rescues_since_progress = 0
        if include_deal:
            summary["deal_page_url"] = f"{origin}/loupan/{summary['xiaoqu_id']}/chengjiao/"
        summaries.append(summary)
        index += 1
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
        print(f"listing_page_url：{summary['listing_page_url']}")
        if summary.get("deal_page_url"):
            print(f"deal_page_url：{summary['deal_page_url']}")

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
        description="房天下小区挂牌页 URL 初始化 MVP（匿名搜索联想 API 版，无浏览器无登录）"
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
    parser.add_argument("--debug", action="store_true", help="打印联想接口原始响应目录（调试用）")
    parser.add_argument(
        "--recover",
        action="store_true",
        help="主链路未命中时启用搜索引擎补漏（真浏览器搜 Bing，默认关闭）",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            city=args.city,
            administrative_district=args.administrative_district,
            community_names=args.community,
            community_district=args.community_district,
            manual_login=args.manual_login,
            debug=args.debug,
            recover=args.recover,
        )
    )


if __name__ == "__main__":
    cli()
