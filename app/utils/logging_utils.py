# -*- coding: utf-8 -*-
"""日志配置。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from threading import RLock

from app.core import config


class DailyFileHandler(logging.Handler):
    """按自然日切换日志文件，适合 7*24 常驻服务。"""

    def __init__(self, log_dir: Path, level: int = logging.INFO, encoding: str = "utf-8"):
        super().__init__(level)
        self.log_dir = log_dir
        self.encoding = encoding
        self._handler: logging.FileHandler | None = None
        self._current_date: str | None = None
        self._lock = RLock()

    def emit(self, record: logging.LogRecord):
        with self._lock:
            self._ensure_handler()
            if self._handler is not None:
                self._handler.emit(record)

    def setFormatter(self, fmt):
        super().setFormatter(fmt)
        if self._handler is not None:
            self._handler.setFormatter(fmt)

    def close(self):
        with self._lock:
            if self._handler is not None:
                self._handler.close()
                self._handler = None
        super().close()

    def _ensure_handler(self):
        current_date = datetime.now().strftime("%Y%m%d")
        if self._handler is not None and self._current_date == current_date:
            return

        if self._handler is not None:
            self._handler.close()

        log_file = self.log_dir / f"{current_date}-info.log"
        handler = logging.FileHandler(log_file, encoding=self.encoding)
        if self.formatter is not None:
            handler.setFormatter(self.formatter)
        handler.setLevel(self.level)
        self._handler = handler
        self._current_date = current_date


def setup_logging(log_level: int = logging.INFO):
    root = logging.getLogger()
    log_dir = config.LOG_DIR
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{datetime.now():%Y%m%d}-info.log"

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    root.setLevel(log_level)
    if getattr(root, "_jeethink_logging_ready", False):
        return log_file

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    file_handler = DailyFileHandler(log_dir=log_dir, level=log_level, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root._jeethink_logging_ready = True
    return log_file
