# -*- coding: utf-8 -*-
"""小区名称规范化和期数分组规则。"""

from __future__ import annotations

import re
from typing import Optional


_SPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[()（）\[\]【】·•,，。]")
_PHASE_RE = re.compile(r"^(?P<base>.*?)(?P<phase>(?:第)?[一二三四五六七八九十百零〇0-9]+期)$")


def normalize_name(value: str) -> str:
    """去掉空白和常见标点，用于稳定查询，不改变展示名称。"""
    text = str(value or "").strip()
    text = _SPACE_RE.sub("", text)
    return _PUNCTUATION_RE.sub("", text)


def split_phase(name: str) -> tuple[str, Optional[str]]:
    """拆分常见的“某某小区一期”名称。"""
    display_name = str(name or "").strip()
    match = _PHASE_RE.match(display_name)
    if not match:
        return display_name, None
    base_name = match.group("base").strip()
    phase = match.group("phase")
    return base_name or display_name, phase


def group_name(name: str) -> str:
    """返回去除期数后的规范化小区组名称。"""
    base_name, _ = split_phase(name)
    return normalize_name(base_name)


def phase_key(phase: Optional[str]) -> str:
    """将空期数转换成可建立唯一索引的稳定键。"""
    return normalize_name(phase or "")
