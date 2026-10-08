# -*- coding: utf-8 -*-
"""链家小区挂牌页 URL 初始化 MVP——匿名 headerSearch API 版（无浏览器、无登录）。

背景：浏览器+账号版 ``lj_community_page_mvp`` 在批量初始化中频繁触发风控并已
发生账号封禁（该脚本 2026-09-30 已删除，不留兜底），链家小区入口初始化统一走
本模块。解析核心在 ``lj_community_page_suggest``（lj/ke 共用）：链家 headerSearch 联想接口
匿名调用，返回候选小区的 communityId / title / region / 别名，无需登录即可完成
“小区名→xiaoqu_id”解析；本模块只负责按链家域名派生入口 URL 并批量编排。

签名与结果字段与统一入口 ``init_community_pages`` 的 ``PLATFORM_MODULES["lj"]``
落库链路（save_community_platform_pages）完全对齐，统一入口零改动。

不做实开挂牌页/成交页复核（避免页面请求暴露自动化特征），入口 URL 按
“挂牌/成交/小区详情共用 id”的链家惯例派生，格式与库内既有数据一致。

用法（独立运行，参数与浏览器版一致）：
  python -m scripts.initialize_community_page.community_page.lj_community_page_mvp \
      --city "深圳" --administrative-district "福田区" --community "绿景虹湾" "香蜜湖一号"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from urllib.parse import urlparse

from app.rpa.platforms.city_map import get_start_url
from app.rpa.utils.logging_utils import setup_logging
from scripts.initialize_community_page.community_page.lj_community_page_suggest import (
    CITY_IDS,
    resolve_community,
)

setup_logging()
log = logging.getLogger(__name__)


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
    """处理单个小区：共享解析核心出 xiaoqu_id，按链家域名派生入口 URL。"""
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

    签名与浏览器版一致：manual_login / manual_close 为兼容参数（API 模式无
    浏览器与登录态，自动忽略）；include_deal=True 时按同 id 惯例派生成交页
    URL。返回逐小区结果摘要（含 success 标记），字段与浏览器版一致。
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
        description="链家小区挂牌页 URL 初始化 MVP（匿名 API 版，无浏览器无登录）"
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
