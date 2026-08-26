# -*- coding: utf-8 -*-
"""从标准输入逐行同步小区记录到 SQLite，并补充腾讯地图坐标。"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.community_data.database import CommunityDatabase
from app.community_data.models import CommunitySeed, GEOCODE_SUCCESS
from app.community_data.service import CommunityDataService


def _result(payload: dict, **values: object) -> None:
    output = {"row_number": payload.get("row_number"), **values}
    print(json.dumps(output, ensure_ascii=False), flush=True)


class RequestRateLimiter:
    """按开始时间节流，确保腾讯地图请求不超过配置 QPS。"""

    def __init__(self, max_qps: float) -> None:
        self._interval = 1 / max_qps
        self._next_request_at = 0.0
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            scheduled_at = max(now, self._next_request_at)
            self._next_request_at = scheduled_at + self._interval
        delay = scheduled_at - now
        if delay > 0:
            time.sleep(delay)


def _sync_one(
    payload: dict,
    service: CommunityDataService,
    rate_limiter: RequestRateLimiter,
    max_attempts: int,
    retry_delay_seconds: float,
) -> dict:
    name = str(payload.get("name") or "").strip()
    administrative_district = str(payload.get("area") or "").strip()
    if not name or not administrative_district:
        return {"status": "PENDING", "message": "缺少小区名或行政区"}

    aliases = tuple(
        alias.strip()
        for alias in str(payload.get("rename") or "").split(",")
        if alias.strip()
    )
    city = str(payload.get("city") or "深圳").strip()
    record = service.add_seed(
        CommunitySeed(
            city=city,
            administrative_district=administrative_district,
            district=str(payload.get("district") or "").strip() or None,
            name=name,
            aliases=aliases,
        )
    )

    last_error = "腾讯地图地理编码失败"
    for attempt in range(max_attempts):
        if record.geocode_status == GEOCODE_SUCCESS:
            return {
                "status": "SUCCESS",
                "community_id": record.community_id,
                "community_group_id": record.community_group_id,
                "longitude": record.longitude,
                "latitude": record.latitude,
                "coordinate_system": record.coordinate_system,
                "geocode_status": record.geocode_status,
            }
        try:
            rate_limiter.wait()
            geocode = service.geocoder.geocode(record.address)
            service.database.update_geocode(record.community_id, geocode)
            return {
                "status": "SUCCESS",
                "community_id": record.community_id,
                "community_group_id": record.community_group_id,
                "longitude": geocode.longitude,
                "latitude": geocode.latitude,
                "coordinate_system": geocode.coordinate_system,
                "geocode_status": GEOCODE_SUCCESS,
            }
        except Exception as exc:  # noqa: BLE001 - 单条失败只保留待重试状态
            last_error = str(exc)[:1000]
            if attempt + 1 < max_attempts:
                time.sleep(retry_delay_seconds * (2**attempt))
            continue

    return {
        "status": "PENDING",
        "community_id": record.community_id,
        "community_group_id": record.community_group_id,
        "geocode_status": record.geocode_status,
        "message": last_error,
    }


def main() -> None:
    database = CommunityDatabase()
    service = CommunityDataService(database=database)
    max_workers = min(max(int(os.getenv("COMMUNITY_SYNC_CONCURRENCY", "5")), 1), 5)
    max_qps = max(float(os.getenv("COMMUNITY_SYNC_QPS", "4")), 0.1)
    max_attempts = max(int(os.getenv("COMMUNITY_SYNC_RETRIES", "5")), 1)
    retry_delay_seconds = max(float(os.getenv("COMMUNITY_SYNC_RETRY_DELAY", "1")), 0)
    payloads = [json.loads(line) for line in sys.stdin if line.strip()]
    pending = False
    rate_limiter = RequestRateLimiter(max_qps)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _sync_one,
                payload,
                service,
                rate_limiter,
                max_attempts,
                retry_delay_seconds,
            ): payload
            for payload in payloads
        }
        for future in as_completed(futures):
            payload = futures[future]
            try:
                values = future.result()
            except Exception as exc:  # noqa: BLE001 - 单条异常保留待重试
                values = {"status": "PENDING", "message": str(exc)[:1000]}
            if values.get("status") != "SUCCESS":
                pending = True
            _result(payload, **values)
    if pending:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
