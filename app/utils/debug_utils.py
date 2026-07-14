# -*- coding: utf-8 -*-
"""RPA 调试辅助。"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

from app.core import config

log = logging.getLogger(__name__)

_debug_override: Optional[bool] = None


def set_debug_mode(enabled: Optional[bool]):
    global _debug_override
    _debug_override = enabled


def is_debug_mode(enabled: Optional[bool] = None) -> bool:
    if enabled is not None:
        return enabled
    if _debug_override is not None:
        return _debug_override
    return config.DEBUG_MODE


def get_debug_dir() -> Path:
    config.DEBUG_DIR.mkdir(exist_ok=True)
    return config.DEBUG_DIR


def _normalize_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", name.strip())
    return cleaned.strip("_") or "page"


async def dump_html(page, name: str, *, enabled: Optional[bool] = None, logger=None) -> Optional[Path]:
    if not is_debug_mode(enabled):
        return None

    active_logger = logger or log
    try:
        debug_dir = get_debug_dir()
        content = await page.get_content()
        out = debug_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{_normalize_name(name)}.html"
        out.write_text(content, encoding="utf-8")
        active_logger.info("html dumped: %s", out)
        return out
    except Exception as exc:
        active_logger.warning("html dump failed: %s -> %s", name, exc)
        return None
