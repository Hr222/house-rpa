# -*- coding: utf-8 -*-
"""按 SQLite 自有主键校准 Excel 小区坐标回写字段。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import CommunityDatabase
from app.community_data.models import CommunityRecord, GEOCODE_SUCCESS


def _exact_record(
    records: list[CommunityRecord],
    city: str,
    administrative_district: str,
    name: str,
) -> CommunityRecord | None:
    """只接受导入时写入的完整身份字段，避免别名匹配到另一小区。"""
    for record in records:
        if (
            record.city == city
            and record.administrative_district == administrative_district
            and record.name == name
        ):
            return record
    return None


def main() -> None:
    database = CommunityDatabase()
    records = database.list_city_with_coordinates("深圳")
    records_by_identity = {
        (record.city, record.administrative_district, record.name): record
        for record in records
    }

    for line in sys.stdin:
        if not line.strip():
            continue
        payload = json.loads(line)
        city = str(payload.get("city") or "深圳").strip()
        administrative_district = str(payload.get("area") or "").strip()
        name = str(payload.get("name") or "").strip()
        record = records_by_identity.get((city, administrative_district, name))
        if record is None:
            result = {
                "row_number": payload.get("row_number"),
                "status": "PENDING",
                "message": "SQLite 未找到完全匹配的小区记录",
            }
        else:
            result = {
                "row_number": payload.get("row_number"),
                "status": "SUCCESS" if record.geocode_status == GEOCODE_SUCCESS else "PENDING",
                "community_id": record.community_id,
                "community_group_id": record.community_group_id,
                "longitude": record.longitude,
                "latitude": record.latitude,
                "coordinate_system": record.coordinate_system,
                "geocode_status": record.geocode_status,
                "message": None,
            }
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
