# -*- coding: utf-8 -*-
"""行舟深房结构化接口响应解析。

本平台没有网页结果页，解析对象是已核对过的 JSON 响应和微信小区索引；
函数保持纯数据转换，方便脱离 WMPF/HTTP 环境单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any, Iterable, Optional

from app.core.models import DealRecord, ListingSnapshot
from app.platforms import xzsfbj_constants as constants


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _unit_price_yuan(value: Any) -> Optional[float]:
    """接口 unitPrice 单位为万元/㎡，统一转换为元/㎡。"""
    parsed = _as_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed * 10000


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[\s\u3000·•,，。.!！？()（）\[\]【】_\-]+", "", text)


def _administrative_district(item: dict[str, Any]) -> str:
    """读取 xqData 的行政区字段；district 是片区，不能混用。"""
    return _normalize_name(
        item.get("area")
        or item.get("administrativeDistrict")
        or item.get("administrative_district")
    )


def is_residential_community(item: dict[str, Any]) -> bool:
    """判断 xqData 条目是否为住宅候选。

    当前索引没有稳定的用途枚举，因此只依据明确的非住宅名称标记；
    不因“大厦”“中心”等泛化字样误删可能的住宅项目。
    """
    name = str(item.get("name") or "").casefold()
    return not any(marker.casefold() in name for marker in constants.NON_RESIDENTIAL_NAME_MARKERS)


def _aliases(item: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("name", "rename", "alias", "aliases", "communityName"):
        value = item.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(str(part) for part in value)
        elif value not in (None, ""):
            # xqData.rename 通常是逗号分隔的别名集合，必须逐个比较，
            # 否则会把整串别名拼接后漏掉用户表中的单个名称。
            values.extend(re.split(r"[,，、;；/|]+", str(value)))
    return tuple(_normalize_name(value) for value in values if _normalize_name(value))


_PHASE_SUFFIX_PATTERN = re.compile(
    r"(?:第?[一二三四五六七八九十百零〇两\d]+期)(?:[东西南北中]区)?$"
)


def _phase_family(value: str) -> str:
    """Remove a trailing phase marker while keeping the community identity."""
    return _PHASE_SUFFIX_PATTERN.sub("", value)


def _has_phase_marker(value: str) -> bool:
    return _PHASE_SUFFIX_PATTERN.search(value) is not None


def find_community_candidates(
    communities: Iterable[dict[str, Any]], requested_name: str,
    administrative_district: Optional[str] = None,
) -> list[dict[str, Any]]:
    """按标准名/别名匹配小区，并可按行政区消歧。

    行政区只比较请求字段与 xqData 的 ``area``，不会把 ``district`` 片区
    或接口返回的其它文本当作小区匹配依据。
    """
    target = _normalize_name(requested_name)
    items = [
        item for item in communities
        if isinstance(item, dict) and is_residential_community(item)
    ]
    if administrative_district not in (None, ""):
        district = _normalize_name(administrative_district)
        if district:
            items = [item for item in items if _administrative_district(item) == district]
    aliases_by_id = {
        id(item): _aliases(item)
        for item in items
    }
    exact = [item for item in items if target and target in aliases_by_id[id(item)]]
    matches = [
        item for item in items
        if target
        and any(
            target in alias or alias in target
            for alias in aliases_by_id[id(item)]
        )
    ]
    if not matches:
        return []

    # A base-name request should include all phase entries when the index
    # splits one residential community across multiple regionIds. Keep the
    # historical exact-alias preference for unrelated similarly named items.
    if not _has_phase_marker(target):
        phase_families = {
            _phase_family(alias)
            for item in matches
            for alias in aliases_by_id[id(item)]
            if _has_phase_marker(alias)
        }
        if exact:
            exact_families = {
                _phase_family(alias)
                for item in exact
                for alias in aliases_by_id[id(item)]
            }
            phase_families &= exact_families
        else:
            phase_families = {
                family
                for family in phase_families
                if target in family or family in target
            }
        if len(phase_families) == 1:
            family = next(iter(phase_families))
            related = [
                item
                for item in items
                if any(
                    _phase_family(alias) == family
                    for alias in aliases_by_id[id(item)]
                )
            ]
            if related:
                return related

    return exact or matches


def parse_community_index(payload: Any) -> list[dict[str, Any]]:
    """从 xqData.json 的常见包装结构中提取小区条目。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("xqData.json 不是对象或数组")
    for key in ("data", "list", "communities", "communityList", "xqList"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    # 有些版本直接以 regionId 为 key 保存条目。
    values = list(payload.values())
    if values and all(isinstance(item, dict) for item in values):
        return values
    raise ValueError("xqData.json 未找到小区列表")


def match_community(
    communities: Iterable[dict[str, Any]], requested_name: str,
    administrative_district: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """返回唯一匹配的小区，多候选或无候选均返回 None。"""
    candidates = find_community_candidates(
        communities, requested_name, administrative_district
    )
    return candidates[0] if len(candidates) == 1 else None


def parse_deal_page(data: Any) -> tuple[int, list[dict[str, Any]]]:
    """解析 getXqDeal.data，返回总条数和当前页成交记录。"""
    if data is None:
        return 0, []
    if not isinstance(data, dict):
        raise ValueError("getXqDeal 的 data 不是对象")
    total = int(_as_float(data.get("count")) or 0)
    records = data.get("dealList", []) or []
    if not isinstance(records, list):
        raise ValueError("getXqDeal 的 dealList 不是列表")
    return total, [item for item in records if isinstance(item, dict)]


def parse_sales(data: Any) -> list[dict[str, Any]]:
    """解析 getCommunitySales.data。"""
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError("getCommunitySales 的 data 不是列表")
    return [item for item in data if isinstance(item, dict)]


def parse_listing_snapshots(
    sales: Iterable[dict[str, Any]], community_name: str,
) -> list[ListingSnapshot]:
    """将接口在售记录转换为统一 ListingSnapshot。"""
    snapshots: list[ListingSnapshot] = []
    for item in sales:
        unit_price = _unit_price_yuan(item.get("unitPrice"))
        if unit_price is None:
            continue
        snapshots.append(
            ListingSnapshot(
                house_id=str(item.get("id") or item.get("houseId") or ""),
                community_name=community_name,
                area=_as_float(item.get("acreage")),
                layout=str(item.get("layout") or ""),
                unit_price=unit_price,
                total_price=_as_float(item.get("price")),
            )
        )
    return snapshots


@dataclass
class XzsfbjDealRecord(DealRecord):
    """行舟深房成交明细；在共享字段上保留平台返回的日期和总价。"""

    date: Optional[str] = None
    total_price: Optional[float] = None


def _normalize_deal_date(value: Any) -> Optional[str]:
    """将接口日期统一为 ``YYYY-MM-DD``；无法识别时返回 ``None``。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", str(value).strip())
    if not match:
        return None
    try:
        return date(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        ).isoformat()
    except ValueError:
        return None


def parse_deal_record(item: dict[str, Any]) -> Optional[XzsfbjDealRecord]:
    """转换单条成交记录；保留日期/总价供近期筛选和日志追溯。"""
    unit_price = _unit_price_yuan(item.get("unitPrice"))
    area = _as_float(item.get("acreage"))
    if unit_price is None:
        return None
    return XzsfbjDealRecord(
        area=area,
        unit_price=unit_price,
        date=_normalize_deal_date(item.get("date")),
        total_price=_as_float(item.get("price")),
    )


def filter_deal_records(
    records: Iterable[dict[str, Any]], area: float, tolerance: float,
    months: int = constants.DEAL_LOOKBACK_MONTHS,
) -> tuple[list[float], list[XzsfbjDealRecord]]:
    """按实际面积和近半年筛选成交，返回价格列表和详情列表。

    口径与链家/房天下一致：面积为目标面积 ``±tolerance``，成交日期不早于
    当前时间往前 ``months`` 个 30 天月；日期缺失或无法识别的记录不参与近期统计。
    """
    if months < 0:
        raise ValueError("成交回溯月数不能为负数")
    cutoff = datetime.now().date() - timedelta(days=30 * months)
    prices: list[float] = []
    parsed: list[XzsfbjDealRecord] = []
    for item in records:
        record = parse_deal_record(item)
        if record is None or record.area is None or record.date is None:
            continue
        if abs(record.area - area) > tolerance:
            continue
        if date.fromisoformat(record.date) < cutoff:
            continue
        prices.append(record.unit_price)
        parsed.append(record)
    return prices, parsed
