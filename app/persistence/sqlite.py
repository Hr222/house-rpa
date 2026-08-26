# -*- coding: utf-8 -*-
"""SQLite 连接和事务的通用生命周期管理。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def sqlite_connection(
    path: Path | str,
    *,
    timeout_seconds: float = 30.0,
    busy_timeout_ms: int = 30_000,
) -> Iterator[sqlite3.Connection]:
    """创建 SQLite 连接，退出时提交或回滚并始终关闭连接。"""
    connection = sqlite3.connect(path, timeout=timeout_seconds)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        with connection:
            yield connection
    finally:
        connection.close()
