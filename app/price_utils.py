from __future__ import annotations

from typing import Optional


def round_price(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 2)


def format_price(value: Optional[float]) -> str:
    if value is None:
        return "None"
    return f"{round_price(value):.2f}"
