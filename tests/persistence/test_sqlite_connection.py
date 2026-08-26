# -*- coding: utf-8 -*-
"""通用 SQLite 连接生命周期测试。"""

from __future__ import annotations

import pytest

from app.persistence import sqlite_connection


def test_sqlite_connection_commits_rolls_back_and_closes(tmp_path) -> None:
    database_path = tmp_path / "sqlite_connection.sqlite3"

    with sqlite_connection(database_path) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records (value) VALUES ('committed')")

    with pytest.raises(RuntimeError, match="rollback"):
        with sqlite_connection(database_path) as connection:
            connection.execute("INSERT INTO records (value) VALUES ('rolled-back')")
            raise RuntimeError("rollback")

    with sqlite_connection(database_path) as connection:
        values = [row["value"] for row in connection.execute("SELECT value FROM records")]

    assert values == ["committed"]
    database_path.unlink()
    assert not database_path.exists()
