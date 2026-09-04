# -*- coding: utf-8 -*-
"""算法层成交记录筛选（从 RPA 剥离，唯一口径来源）。

RPA 只回传原始成交明细（deal_records: {area, date, total_price, price}）；
本模块负责把原始成交按请求面积与平台口径收敛为成交单价列表，供
aggregation 组装 AlgorithmInput.deal_price_lists。平台差异只在
"成交面积容差 + 是否筛近半年"两个参数（口径现状：统一严格面积区间，
贝壳成交少不额外筛日期，链家/房天下加近半年，行舟深房 ±1㎡ + 近半年）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional

from app.algorithm.area_rules import DEAL_AREA_TOLERANCE

# 平台成交口径表：name(PlatformResult.name) -> (面积容差㎡, 近半年月数/None 不筛日期)
DEAL_SCREENING_RULES: dict[str, dict] = {
    "贝壳": {"tolerance": DEAL_AREA_TOLERANCE, "months": None},
    "链家": {"tolerance": DEAL_AREA_TOLERANCE, "months": 6},
    "房天下": {"tolerance": DEAL_AREA_TOLERANCE, "months": 6},
    "行舟深房": {"tolerance": 1.0, "months": 6},
}
DEFAULT_RULE = {"tolerance": DEAL_AREA_TOLERANCE, "months": 6}


def _cutoff_str(months: int) -> str:
    return (datetime.now() - timedelta(days=30 * months)).strftime("%Y-%m-%d")


def screen_deal_records(
    records: Iterable[Mapping],
    request_area: float,
    *,
    platform: Optional[str] = None,
    tolerance: Optional[float] = None,
    months: Optional[int] = None,
) -> list[float]:
    """把原始成交记录收敛为请求面积口径内的成交单价列表。

    records: [{area, date, total_price, price}, ...]（RPA 原始抓取）。
    platform: PlatformResult.name，用于取该平台口径；也可显式传
    tolerance/months 覆盖。area 缺失或不在区间内的记录不进入统计；
    筛选日期时（months 非空）日期缺失或早于近半年的记录剔除（与
    原 RPA parser 口径一致：日期缺失不保留）。返回 [price, ...]。
    """
    rule = dict(DEFAULT_RULE)
    rule.update(DEAL_SCREENING_RULES.get(platform or "", {}))
    if tolerance is not None:
        rule["tolerance"] = tolerance
    if months is not None:
        rule["months"] = months
    from app.algorithm.area_rules import deal_area_bounds

    area_min, area_max = deal_area_bounds(request_area, rule["tolerance"])
    months = rule["months"]
    cutoff = _cutoff_str(months) if months else None

    prices: list[float] = []
    for record in records:
        area = record.get("area")
        price = record.get("price")
        if area is None or price is None:
            continue
        if not (area_min <= float(area) <= area_max):
            continue
        date_str = record.get("date")
        if cutoff is not None and date_str and str(date_str) < cutoff:
            continue
        try:
            value = float(price)
        except (TypeError, ValueError):
            continue
        if value > 0:
            prices.append(value)
    return prices
