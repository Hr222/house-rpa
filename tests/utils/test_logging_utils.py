# -*- coding: utf-8 -*-
"""日志按级别和日期写入文件的测试。"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core import config
from app.utils.logging_utils import DailyFileHandler, setup_logging


def test_daily_file_handler_writes_only_configured_level(tmp_path):
    handler = DailyFileHandler(
        tmp_path,
        level=logging.WARNING,
        filename_suffix="error",
    )
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.logging_utils.error_file")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)

    try:
        logger.info("should stay out")
        logger.warning("warning is recorded")
        logger.error("error is recorded")
    finally:
        logger.removeHandler(handler)
        handler.close()

    log_file = tmp_path / f"{datetime.now():%Y%m%d}-error.log"
    content = log_file.read_text(encoding="utf-8")
    assert "should stay out" not in content
    assert "WARNING warning is recorded" in content
    assert "ERROR error is recorded" in content


def test_setup_logging_registers_info_and_error_files(tmp_path, monkeypatch):
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    old_ready = getattr(root, "_jeethink_logging_ready", None)

    for handler in old_handlers:
        handler.close()
    root.handlers.clear()
    root.__dict__.pop("_jeethink_logging_ready", None)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)

    try:
        setup_logging()
        logger = logging.getLogger("test.logging_utils.setup")
        logger.info("info record")
        logger.warning("warning record")

        date = datetime.now().strftime("%Y%m%d")
        info_content = (tmp_path / f"{date}-info.log").read_text(encoding="utf-8")
        error_content = (tmp_path / f"{date}-error.log").read_text(encoding="utf-8")
        assert "info record" in info_content
        assert "warning record" in info_content
        assert "info record" not in error_content
        assert "warning record" in error_content
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers.clear()
        root.handlers.extend(old_handlers)
        root.setLevel(old_level)
        if old_ready is None:
            root.__dict__.pop("_jeethink_logging_ready", None)
        else:
            root._jeethink_logging_ready = old_ready
