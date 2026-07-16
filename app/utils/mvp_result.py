# -*- coding: utf-8 -*-
"""MVP 测试结果统一输出。所有平台 MVP 脚本共用。"""

from __future__ import annotations

import logging
from typing import Optional

from app.core import config
from app.core.price_utils import format_price, round_price

log = logging.getLogger("mvp-result")


def print_mvp_result(
    *,
    platform: str,
    community_name: str,
    area: float,
    trace: dict,
    listings: dict,
    deals: dict,
    result: dict,
    elapsed: float,
):
    """统一输出 MVP 测试结果。

    Args:
        platform: 平台名称（贝壳/安居客/房天下/乐有家/链家）
        trace: {"home_blocked", "search_url", "area_ok", "area_url", "area_pages",
                "detail_ok", "detail_url"}
        listings: {"count", "avg", "snapshots"}
        deals: {"count", "avg", "records", "substitute"}
            - records: 成交记录列表（有成交的平台）
            - substitute: 挂牌均价/小区均价替代说明（无成交的平台如安居客/乐有家）
        result: {"quote_avg", "deal_avg", "final_price", "branch"}
    """
    lines = []
    lines.append("=" * 60)
    lines.append(f"{platform} MVP 测试结果")
    lines.append("=" * 60)
    lines.append(f"小区: {community_name}")
    lines.append(f"面积: {area:.1f} ㎡")
    lines.append("")

    # ---- 流程跟踪 ----
    lines.append("----- 流程跟踪 -----")
    lines.append(f"首页:    被拦={trace.get('home_blocked', False)}")
    lines.append(f"搜索后:  URL={trace.get('search_url', '')}")
    lines.append(f"面积筛选: 成功={trace.get('area_ok', False)}  URL={trace.get('area_url', '')}  "
                 f"总页数={trace.get('area_pages', 0)}")
    if trace.get("detail_url"):
        lines.append(f"详情页:  点击={trace.get('detail_ok', False)}  URL={trace.get('detail_url', '')}")

    # ---- 数据汇总 ----
    lines.append("----- 数据汇总 -----")
    lines.append(f"在售条数: {listings['count']}")
    if listings.get("avg"):
        lines.append(f"在售均价: {format_price(listings['avg'])} 元/㎡")
    if deals.get("avg"):
        lines.append(f"成交条数: {deals['count']}")
        lines.append(f"成交均价: {format_price(deals['avg'])} 元/㎡")
    lines.append(f"决策分支: {result['branch']}")
    lines.append(f"最终取值: {format_price(result['final_price'])} 元/㎡")
    if elapsed:
        lines.append(f"耗时: {elapsed:.0f}s")

    # ---- 在售房源 ----
    snapshots = listings.get("snapshots", [])
    if snapshots:
        lines.append("")
        lines.append("-" * 60)
        for s in snapshots:
            lines.append(
                f"{platform}: "
                f"{{小区名称: {s.community_name or ''}, 面积: {s.area or ''}平米, "
                f"几房几厅: {s.layout or ''}, 售价: {s.unit_price or ''}元/平, "
                f"总价: {s.total_price or ''}万}}"
            )
        lines.append("-" * 60)

    # ---- 成交记录 ----
    deal_records = deals.get("records", [])
    substitute = deals.get("substitute", "")
    if deal_records:
        lines.append("")
        lines.append("-" * 60)
        for r in deal_records:
            lines.append(
                f"{platform}成交: "
                f"{{面积: {r.area or ''}㎡, "
                f"单价: {r.unit_price or ''}元/平}}"
            )
        lines.append("-" * 60)
    elif substitute:
        lines.append(f"\n成交记录: 无（{substitute}）")
    else:
        lines.append("\n成交记录: 无")

    # ---- 模拟返回 ----
    lines.append("")
    lines.append("模拟返回 body:")
    lines.append(
        "{"
        f'"quoteAvg": {format_price(result["quote_avg"])}, '
        f'"dealAvg": {format_price(result["deal_avg"])}, '
        f'"finalPrice": {format_price(result["final_price"])}'
        "}"
    )
    lines.append("=" * 60)

    # 一条多行日志输出到 console + 文件
    log.info("\n" + "\n".join(lines))
