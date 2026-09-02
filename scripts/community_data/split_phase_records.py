# -*- coding: utf-8 -*-
"""把合并了期数的小区主数据拆分为期数记录，并补录对应平台页面入口。

背景：部分小区主数据把多期合并为一条记录，而平台按期数拆分页面，
导致平台页面在主数据无落点（如尚都二期、海上世界双玺一期、中信红树湾
北区的乐有家三/四/五期页）。本脚本按人工核对结论执行两类操作：

1. 主数据拆分：父记录改为具体期数（或保持原名仅清理期数别名），
   新增缺失期数记录，坐标与行政区继承父记录；建成年份无把握的留空待回填。
2. 平台页面补录：为新期数记录写入人工核对过的期数页，并把确认为
   合并页的已有入口双写到新记录（沿用锦绣江南等已验证的双写模式）。

幂等：已改名的父记录、已存在的目标记录和已入库的页面会跳过，
可重复执行。改动同步记录在 sql/split_phase_records_20260902.sql。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import DEFAULT_DB_PATH
from app.community_data.normalization import normalize_name, phase_key
from app.persistence.sqlite import sqlite_connection


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewPhaseRecord:
    """拆分后新增的一条期数记录。"""

    name: str
    phase: Optional[str]
    aliases: tuple[str, ...] = ()
    build_year: Optional[int] = None


@dataclass(frozen=True)
class SplitSpec:
    """一条合并记录的拆分规格。"""

    parent_id: int
    note: str
    parent_new_name: Optional[str] = None
    parent_phase: Optional[str] = None
    parent_aliases: Optional[tuple[str, ...]] = None
    new_records: tuple[NewPhaseRecord, ...] = ()


SPLIT_SPECS = (
    SplitSpec(
        parent_id=2146,
        note="海上世界双玺：主数据原合并一+二期；lyj二期页已落2146，故2146改为二期，新增一期",
        parent_new_name="海上世界双玺二期",
        parent_phase="二期",
        parent_aliases=("海上世界双玺花园二期", "双玺花园二期"),
        new_records=(
            NewPhaseRecord(
                name="海上世界双玺一期",
                phase="一期",
                aliases=("海上世界双玺花园一期",),
                build_year=None,
            ),
        ),
    ),
    SplitSpec(
        parent_id=3280,
        note="尚都：主数据原合并一+二期；ajk一期页487114与lj/ke一期页已落3280，故3280改为一期，新增二期",
        parent_new_name="尚都一期",
        parent_phase="一期",
        parent_aliases=("尚都花园", "鸿荣源尚都", "尚都·新天地"),
        new_records=(
            NewPhaseRecord(name="尚都二期", phase="二期", build_year=None),
        ),
    ),
    SplitSpec(
        parent_id=3201,
        note="中洲华府：主数据原合并一+二期；ajk一期页381927已落3201，故3201改为一期，新增二期；中洲中央公园别名族暂留一期待人工鉴别",
        parent_new_name="中洲华府一期",
        parent_phase="一期",
        parent_aliases=(
            "中洲中央公园",
            "中洲中央1街",
            "中洲中央公园一期",
            "中洲中央公园2期",
        ),
        new_records=(
            NewPhaseRecord(name="中洲华府二期", phase="二期", build_year=None),
        ),
    ),
    SplitSpec(
        parent_id=2048,
        note="中信红树湾北区：主数据一条覆盖三/四/五期且lyj为三个独立页面；2048保留北区本体，新增三期/四期/五期",
        parent_aliases=("中信红树湾花城北区",),
        new_records=(
            NewPhaseRecord(name="中信红树湾三期", phase="三期", build_year=2009),
            NewPhaseRecord(name="中信红树湾四期", phase="四期", build_year=2009),
            NewPhaseRecord(name="中信红树湾五期", phase="五期", build_year=2009),
        ),
    ),
    SplitSpec(
        parent_id=3529,
        note="联投东方华府：主数据只有一期；按ajk人工核对页补二期/三期",
        new_records=(
            NewPhaseRecord(name="联投东方华府二期", phase="二期", build_year=None),
            NewPhaseRecord(name="联投东方华府三期", phase="三期", build_year=None),
        ),
    ),
    SplitSpec(
        parent_id=2095,
        note="中信红树湾南区：按业务口径南区=一+二期，整体保留一条，补一期别名（轻量方案，不拆记录）",
        parent_aliases=("中信红树湾二期", "中信红树湾花城南区", "中信红树湾一期"),
    ),
    SplitSpec(
        parent_id=5670,
        note="鸿荣源珈誉府：按ajk人工核对页补3区（非期数，无phase）",
        new_records=(
            NewPhaseRecord(name="鸿荣源珈誉府3区", phase=None, build_year=None),
        ),
    ),
)


@dataclass(frozen=True)
class PlatformPageRow:
    """一条待补录的平台页面入口，record 用名称定位（含新增记录）。"""

    record_name: str
    platform: str
    source_community_name: str
    listing_page_url: str
    note: str = ""


PLATFORM_PAGE_ROWS = (
    # ---- 尚都二期 ----
    PlatformPageRow("尚都二期", "ajk", "尚都二期", "https://shenzhen.anjuke.com/sale?comm_id=1026635", "ajk人工核对"),
    PlatformPageRow("尚都二期", "ke", "尚都二期", "https://sz.ke.com/ershoufang/c2414233347044137", "lj人工核对（与ke同页）"),
    PlatformPageRow("尚都二期", "lj", "尚都二期", "https://sz.lianjia.com/ershoufang/c2414233347044137", "lj人工核对"),
    PlatformPageRow("尚都二期", "fang", "尚都", "https://sz.esf.fang.com/house-xm2810076262", "合并页双写"),
    PlatformPageRow("尚都二期", "lyj", "鸿荣源尚都", "https://shenzhen.leyoujia.com/esf?b=48", "合并页双写"),
    # ---- 海上世界双玺一期 ----
    PlatformPageRow("海上世界双玺一期", "lyj", "海上世界双玺", "https://shenzhen.leyoujia.com/esf?b=38533", "lyj人工核对"),
    PlatformPageRow("海上世界双玺一期", "ajk", "海上世界双玺", "https://shenzhen.anjuke.com/sale?comm_id=616637", "合并页双写"),
    PlatformPageRow("海上世界双玺一期", "fang", "海上世界双玺", "https://sz.esf.fang.com/house-xm2811130750", "合并页双写"),
    PlatformPageRow("海上世界双玺一期", "ke", "海上世界双玺", "https://sz.ke.com/ershoufang/c246944473492728", "合并页双写"),
    PlatformPageRow("海上世界双玺一期", "lj", "海上世界双玺", "https://sz.lianjia.com/ershoufang/c246944473492728", "合并页双写"),
    # ---- 中洲华府二期 ----
    PlatformPageRow("中洲华府二期", "ajk", "中洲华府二期", "https://shenzhen.anjuke.com/sale?comm_id=907391", "ajk人工核对"),
    PlatformPageRow("中洲华府二期", "fang", "中洲华府", "https://sz.esf.fang.com/house-xm2810209528", "合并页双写"),
    PlatformPageRow("中洲华府二期", "ke", "中洲华府", "https://sz.ke.com/ershoufang/c2411049238509", "合并页双写"),
    PlatformPageRow("中洲华府二期", "lj", "中洲华府", "https://sz.lianjia.com/ershoufang/c2411049238509", "合并页双写"),
    PlatformPageRow("中洲华府二期", "lyj", "中洲华府", "https://shenzhen.leyoujia.com/esf?b=54049", "合并页双写"),
    # ---- 中信红树湾三期/四期/五期（lyj独立页）----
    PlatformPageRow("中信红树湾三期", "lyj", "中信红树湾三期", "https://shenzhen.leyoujia.com/esf?b=1002", "lyj人工核对"),
    PlatformPageRow("中信红树湾四期", "lyj", "中信红树湾四期", "https://shenzhen.leyoujia.com/esf?b=272", "lyj人工核对"),
    PlatformPageRow("中信红树湾五期", "lyj", "中信红树湾五期", "https://shenzhen.leyoujia.com/esf?b=704120", "lyj人工核对"),
    # ---- 中信红树湾北区合并页（ke/lj/fang）双写到三个新期数记录 ----
    PlatformPageRow("中信红树湾三期", "ke", "中信红树湾北区", "https://sz.ke.com/ershoufang/c2411049784546", "北区合并页双写"),
    PlatformPageRow("中信红树湾三期", "lj", "中信红树湾北区", "https://sz.lianjia.com/ershoufang/c2411049784546", "北区合并页双写"),
    PlatformPageRow("中信红树湾三期", "fang", "中信红树湾北区", "https://sz.esf.fang.com/house-xm2811074244", "北区合并页双写"),
    PlatformPageRow("中信红树湾四期", "ke", "中信红树湾北区", "https://sz.ke.com/ershoufang/c2411049784546", "北区合并页双写"),
    PlatformPageRow("中信红树湾四期", "lj", "中信红树湾北区", "https://sz.lianjia.com/ershoufang/c2411049784546", "北区合并页双写"),
    PlatformPageRow("中信红树湾四期", "fang", "中信红树湾北区", "https://sz.esf.fang.com/house-xm2811074244", "北区合并页双写"),
    PlatformPageRow("中信红树湾五期", "ke", "中信红树湾北区", "https://sz.ke.com/ershoufang/c2411049784546", "北区合并页双写"),
    PlatformPageRow("中信红树湾五期", "lj", "中信红树湾北区", "https://sz.lianjia.com/ershoufang/c2411049784546", "北区合并页双写"),
    PlatformPageRow("中信红树湾五期", "fang", "中信红树湾北区", "https://sz.esf.fang.com/house-xm2811074244", "北区合并页双写"),
    # ---- 联投东方华府二期/三期、珈誉府3区 ----
    PlatformPageRow("联投东方华府二期", "ajk", "联投东方华府(二期)", "https://shenzhen.anjuke.com/sale?comm_id=611969", "ajk人工核对"),
    PlatformPageRow("联投东方华府三期", "ajk", "联投东方华府(三期)", "https://shenzhen.anjuke.com/sale?comm_id=1793433", "ajk人工核对"),
    PlatformPageRow("鸿荣源珈誉府3区", "ajk", "鸿荣源珈誉府3区", "https://shenzhen.anjuke.com/sale?comm_id=1994397", "ajk人工核对"),
    # ---- 已有记录的独立补录（ajk人工核对文件中已核对、库内缺行）----
    PlatformPageRow("合正新悦启园", "ajk", "合正新悦启园", "https://shenzhen.anjuke.com/sale?comm_id=96706", "独立补录"),
    PlatformPageRow("日出印象一期", "ajk", "日出印象(一期)", "https://shenzhen.anjuke.com/sale?comm_id=177591", "独立补录"),
    PlatformPageRow("卓越和奕府一期", "ajk", "卓越和奕府(一期)", "https://shenzhen.anjuke.com/sale?comm_id=1892861", "独立补录"),
    PlatformPageRow("信义金御半山二期", "ajk", "信义金御半山二期", "https://shenzhen.anjuke.com/sale?comm_id=1176458", "独立补录"),
    PlatformPageRow("信义金御半山三期", "ajk", "信义金御半山三期", "https://shenzhen.anjuke.com/sale?comm_id=1019193", "独立补录"),
    PlatformPageRow("信义金御半山五期", "ajk", "信义金御半山五期", "https://shenzhen.anjuke.com/sale?comm_id=1908362", "独立补录"),
    PlatformPageRow("信义金御半山珑门", "ajk", "信义金御半山珑门", "https://shenzhen.anjuke.com/sale?comm_id=1919751", "独立补录"),
    PlatformPageRow("朗泓龙园大观", "ajk", "朗泓龙园大观(一期)", "https://shenzhen.anjuke.com/sale?comm_id=800964", "独立补录"),
    PlatformPageRow("世纪春城一期", "ajk", "世纪春城(一期)", "https://shenzhen.anjuke.com/sale?comm_id=323584", "独立补录"),
    PlatformPageRow("世纪春城二期", "ajk", "世纪春城(二期)", "https://shenzhen.anjuke.com/sale?comm_id=97566", "独立补录"),
    PlatformPageRow("世纪春城四期", "ajk", "世纪春城(四期)", "https://shenzhen.anjuke.com/sale?comm_id=97606", "独立补录"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch_parent(connection, community_id: int):
    return connection.execute(
        "SELECT * FROM communities WHERE community_id = ?",
        (community_id,),
    ).fetchone()


def _find_record_id(connection, city: str, district: str, name: str, phase: Optional[str]) -> Optional[int]:
    row = connection.execute(
        """
        SELECT community_id FROM communities
        WHERE city = ? AND administrative_district = ?
          AND normalized_name = ? AND phase_key = ?
        """,
        (city, district, normalize_name(name), phase_key(phase)),
    ).fetchone()
    return row["community_id"] if row else None


def apply_master_split(connection, spec: SplitSpec, dry_run: bool) -> None:
    parent = _fetch_parent(connection, spec.parent_id)
    if parent is None:
        raise RuntimeError(f"父记录不存在: community_id={spec.parent_id}")
    parent_id = parent["community_id"]
    print(f"[split] 父记录 id={parent_id} name={parent['name']} — {spec.note}")
    new_address_city = (
        parent["city"] if parent["city"].endswith("市") else f"{parent['city']}市"
    )

    if spec.parent_new_name and parent["name"] != spec.parent_new_name:
        conflicting = _find_record_id(
            connection,
            parent["city"],
            parent["administrative_district"],
            spec.parent_new_name,
            spec.parent_phase,
        )
        if conflicting is not None and conflicting != parent_id:
            raise RuntimeError(
                f"改名冲突: {spec.parent_new_name} 已被 community_id={conflicting} 占用"
            )
        new_address = f"{new_address_city}{parent['administrative_district']}{spec.parent_new_name}"
        print(
            f"  [rename] {parent['name']} -> {spec.parent_new_name} "
            f"phase={spec.parent_phase} aliases={list(spec.parent_aliases or ())}"
        )
        if not dry_run:
            connection.execute(
                """
                UPDATE communities
                SET name = ?, normalized_name = ?, phase = ?, phase_key = ?,
                    aliases_json = ?, address = ?, updated_at = ?
                WHERE community_id = ?
                """,
                (
                    spec.parent_new_name,
                    normalize_name(spec.parent_new_name),
                    spec.parent_phase,
                    phase_key(spec.parent_phase),
                    json.dumps(list(spec.parent_aliases or ()), ensure_ascii=False),
                    new_address,
                    _now(),
                    parent_id,
                ),
            )
    elif spec.parent_aliases is not None:
        current_aliases = json.loads(parent["aliases_json"] or "[]")
        if current_aliases != list(spec.parent_aliases):
            print(f"  [aliases] {parent['name']} 别名清理为 {list(spec.parent_aliases)}")
            if not dry_run:
                connection.execute(
                    "UPDATE communities SET aliases_json = ?, updated_at = ? WHERE community_id = ?",
                    (
                        json.dumps(list(spec.parent_aliases), ensure_ascii=False),
                        _now(),
                        parent_id,
                    ),
                )

    for new_record in spec.new_records:
        existing_id = _find_record_id(
            connection,
            parent["city"],
            parent["administrative_district"],
            new_record.name,
            new_record.phase,
        )
        if existing_id is not None:
            print(f"  [exists] {new_record.name} 已存在 id={existing_id}，跳过")
            continue
        print(
            f"  [insert] {new_record.name} phase={new_record.phase} "
            f"year={new_record.build_year or parent['build_year'] or '待回填'} 组={parent['community_group_id']}"
        )
        if not dry_run:
            inherit_year = (
                new_record.build_year
                if new_record.build_year is not None
                else parent["build_year"]
            )
            cursor = connection.execute(
                """
                INSERT INTO communities (
                    community_group_id, city, administrative_district, district,
                    name, normalized_name, phase, phase_key, aliases_json,
                    build_year, address, longitude, latitude, coordinate_system,
                    geocode_status, geocode_level, geocode_reliability,
                    estate_type, created_at, updated_at
                )
                SELECT community_group_id, city, administrative_district, district,
                       ?, ?, ?, ?, ?, ?, ?, longitude, latitude, coordinate_system,
                       geocode_status, geocode_level, geocode_reliability,
                       estate_type, ?, ?
                FROM communities WHERE community_id = ?
                """,
                (
                    new_record.name,
                    normalize_name(new_record.name),
                    new_record.phase,
                    phase_key(new_record.phase),
                    json.dumps(list(new_record.aliases), ensure_ascii=False),
                    inherit_year,
                    f"{new_address_city}{parent['administrative_district']}{new_record.name}",
                    _now(),
                    _now(),
                    parent_id,
                ),
            )
            print(f"    -> 新 community_id={cursor.lastrowid}")


def resolve_record_id(connection, record_name: str) -> int:
    """按正式名定位记录；名字不唯一时拒绝，避免补录错行。"""
    rows = connection.execute(
        """
        SELECT community_id FROM communities
        WHERE city = ? AND normalized_name = ?
        """,
        ("深圳", normalize_name(record_name)),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["community_id"]
    if not rows:
        raise RuntimeError(f"补录页面时找不到记录: {record_name}")
    raise RuntimeError(f"记录名不唯一，需改用 community_id 定位: {record_name}")


def apply_platform_pages(dry_run: bool) -> None:
    from app.property_records.ingestion import PropertyRecordsIngestion

    ingestion = PropertyRecordsIngestion()
    id_cache: dict[str, int] = {}

    with sqlite_connection(DEFAULT_DB_PATH) as connection:
        for row in PLATFORM_PAGE_ROWS:
            try:
                if row.record_name not in id_cache:
                    id_cache[row.record_name] = resolve_record_id(connection, row.record_name)
                community_id: Optional[int] = id_cache[row.record_name]
            except RuntimeError:
                if not dry_run:
                    raise
                community_id = None
            label = f"id={community_id}" if community_id is not None else "id=待拆分新增"
            print(
                f"[page] {row.record_name}({label}) {row.platform} "
                f"{row.listing_page_url} — {row.note}"
            )
            if dry_run or community_id is None:
                continue
            ingestion.ingest_platform_result(
                community_id=community_id,
                city="深圳",
                administrative_district=_district_of(connection, community_id),
                source_platform=row.platform,
                source_community_name=row.source_community_name,
                listing_page_url=row.listing_page_url,
            )


def _district_of(connection, community_id: int) -> str:
    row = connection.execute(
        "SELECT administrative_district FROM communities WHERE community_id = ?",
        (community_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"记录不存在: community_id={community_id}")
    return row["administrative_district"]


# 组归并：把割裂的组并入统一组，使“一个小区一个组”成立。
# (目标组, 被并入的组, 说明)
GROUP_MERGES = (
    (
        5233,
        (2048, 2095),
        "中信红树湾：整体/南区/北区及三/四/五期统一归入组5233，附近查询不再同楼盘串味",
    ),
)


def apply_group_merge(connection, target_group_id: int, source_group_ids, note: str, dry_run: bool) -> None:
    placeholders = ",".join("?" for _ in source_group_ids)
    print(f"[group] 组{source_group_ids} -> 组{target_group_id} — {note}")
    if dry_run:
        return
    moved = connection.execute(
        f"""
        UPDATE communities
        SET community_group_id = ?, updated_at = ?
        WHERE city = '深圳' AND community_group_id IN ({placeholders})
        """,
        (target_group_id, _now(), *source_group_ids),
    ).rowcount
    for group_id in source_group_ids:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM communities WHERE community_group_id = ?",
            (group_id,),
        ).fetchone()[0]
        if remaining:
            raise RuntimeError(f"组{group_id}下仍有{remaining}条记录，中止删除组行")
        connection.execute(
            "DELETE FROM community_groups WHERE community_group_id = ?",
            (group_id,),
        )
    print(f"  -> 迁移 {moved} 条记录，清理空组行 {list(source_group_ids)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="拆分合并期数的小区主数据并补录平台页面（幂等）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将执行的变更，不写库",
    )
    parser.add_argument(
        "--skip-pages",
        action="store_true",
        help="只执行主数据拆分，跳过平台页面补录",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = parse_args()
    if args.dry_run:
        print("=== DRY-RUN：不写库 ===")
    with sqlite_connection(DEFAULT_DB_PATH) as connection:
        for spec in SPLIT_SPECS:
            apply_master_split(connection, spec, args.dry_run)
        for target_group_id, source_group_ids, note in GROUP_MERGES:
            apply_group_merge(connection, target_group_id, source_group_ids, note, args.dry_run)
    if not args.skip_pages:
        apply_platform_pages(args.dry_run)
    print("=== 完成 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
