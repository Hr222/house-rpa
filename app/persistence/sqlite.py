# -*- coding: utf-8 -*-
"""SQLite 连接和事务的通用生命周期管理。

提供全项目统一的 SQL 操作日志（轻量 CRUD 追踪，无第三方依赖）：
使用 sqlite3 原生的 set_trace_callback 在每个 execute 后记录 SQL——
写操作（INSERT/UPDATE/DELETE/REPLACE/UPSERT）与 DDL 记 INFO，
读操作（SELECT/PRAGMA 等）记 DEBUG（避免噪音，需要时把 logger
`app.persistence.sql` 调到 DEBUG 即可）。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SQL_LOG = logging.getLogger("app.persistence.sql")

_WRITE_VERBS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "UPSERT"}
_DDL_VERBS = {"CREATE", "ALTER", "DROP", "VACUUM", "REINDEX"}


def _compact(sql: str, limit: int) -> str:
    """把多行 SQL 压成单行并截断，便于日志阅读。"""
    one_line = " ".join((sql or "").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "..."


def _sql_trace(sql: str) -> None:
    """sqlite3 trace 回调：按操作类型分级别记录 SQL。"""
    statement = (sql or "").strip()
    if not statement:
        return
    verb = statement.split(None, 1)[0].upper()
    if verb in _WRITE_VERBS:
        _SQL_LOG.info("SQL %s: %s", verb, _compact(statement, 300))
    elif verb in _DDL_VERBS:
        _SQL_LOG.info("DDL %s: %s", verb, _compact(statement, 200))
    else:
        _SQL_LOG.debug("SQL %s", _compact(statement, 200))


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
        connection.set_trace_callback(_sql_trace)
        with connection:
            yield connection
    finally:
        connection.close()
