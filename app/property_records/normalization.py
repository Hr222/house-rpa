# -*- coding: utf-8 -*-
"""房源记录的日期、金额、面积和 URL 标准化。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re


_TWO_PLACES = Decimal("0.01")
_MONEY_PLACES = Decimal("0.01")
_TRACKING_QUERY_KEYS = {"from", "source", "spm", "ref", "share"}


def normalize_date(value: Any) -> str:
    """将日期转换为 YYYY-MM-DD。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        raise ValueError(f"成交日期格式不支持: {value!r}")
    try:
        return date(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        ).isoformat()
    except ValueError as exc:
        raise ValueError(f"成交日期不合法: {value!r}") from exc


def normalize_area_sqm(value: Any) -> float:
    """将面积转换为平方米并保留两位小数。"""
    number = _decimal_number(value, "建筑面积")
    number = number.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    if number <= 0:
        raise ValueError("建筑面积必须大于 0")
    return float(number)


def normalize_total_price_yuan(value: Any) -> float:
    """将总价转换为元并保留两位小数。

    字符串带“万”时按万元转换；带“元”或没有单位时按元处理。
    无单位的数字输入也按元处理，避免隐式猜测单位。
    """
    text = str(value or "").strip().replace(",", "")
    if not text:
        raise ValueError("成交总价不能为空")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        raise ValueError(f"成交总价格式不支持: {value!r}")
    number = _decimal_number(match.group(0), "成交总价")
    if "万" in text:
        number *= Decimal("10000")
    number = number.quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)
    if number <= 0:
        raise ValueError("成交总价必须大于 0")
    return float(number)


def normalize_unit_price_yuan(value: Any) -> float:
    """将单价转换为元/平方米并保留两位小数。"""
    number = _decimal_number(value, "成交单价")
    number = number.quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)
    if number <= 0:
        raise ValueError("成交单价必须大于 0")
    return float(number)


def normalize_listing_url(value: Any) -> str:
    """规范化单套房源详情 URL，保留业务参数，去掉常见追踪参数。"""
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"挂牌 URL 必须是完整 HTTP(S) 地址: {value!r}")
    query = []
    for key, query_value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in _TRACKING_QUERY_KEYS:
            continue
        query.append((key, query_value))
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query),
            "",
        )
    )


def normalize_page_url(value: Any, *, keep_trailing_slash: bool = False) -> str:
    """规范化小区列表入口 URL，保留业务查询参数。

    keep_trailing_slash=True 时保留**无 query 路径型 URL** 的尾斜杠
    （成交页地址口径：链家 /chengjiao/c{id}/、房天下 /loupan/{id}/chengjiao/
    带尾斜杠与页面一致，均无 query）；带 query 的 URL 仍统一剥 path
    尾斜杠（query 参数才是关键，尾斜杠无语义）。挂牌页地址维持无尾
    斜杠存储（keep_trailing_slash 默认 False）。
    """
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"页面 URL 必须是完整 HTTP(S) 地址: {value!r}")
    path = parts.path or "/"
    if path != "/" and not (keep_trailing_slash and not parts.query):
        path = path.rstrip("/")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            parts.query,
            "",
        )
    )


def _decimal_number(value: Any, field_name: str) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    if not text:
        raise ValueError(f"{field_name}不能为空")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        if not match:
            raise ValueError(f"{field_name}格式不支持: {value!r}")
        try:
            number = Decimal(match.group(0))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name}格式不支持: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{field_name}必须是有限数字")
    return number
