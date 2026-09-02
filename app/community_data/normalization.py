# -*- coding: utf-8 -*-
"""小区名称规范化和期数分组规则。"""

from __future__ import annotations

import re
from typing import Optional


_SPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[()（）\[\]【】·•,，。]")
_PHASE_RE = re.compile(r"^(?P<base>.*?)(?P<phase>(?:第)?[零〇一二三四五六七八九十百0-9]+期)$")
_PHASE_IN_PAREN_RE = re.compile(
    r"^(?P<base>.+?)[（(](?P<phase>(?:第)?[零〇一二三四五六七八九十百0-9]+期)[）)]$"
)
_PAREN_SUB_NAME_RE = re.compile(r"^(?P<main>.+?)[（(](?P<inner>[^（()）]+)[）)]$")
_CN_DIGIT_VALUES = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def normalize_name(value: str) -> str:
    """去掉空白和常见标点，用于稳定查询，不改变展示名称。"""
    text = str(value or "").strip()
    text = _SPACE_RE.sub("", text)
    return _PUNCTUATION_RE.sub("", text)


def split_phase(name: str) -> tuple[str, Optional[str]]:
    """拆分常见的“某某小区一期”名称，支持尾部括号期数如“某某花园(二期)”。"""
    display_name = str(name or "").strip()
    match = _PHASE_IN_PAREN_RE.match(display_name) or _PHASE_RE.match(display_name)
    if not match:
        return display_name, None
    base_name = match.group("base").strip()
    phase = match.group("phase")
    return base_name or display_name, phase


def phase_to_number(phase: Optional[str]) -> Optional[int]:
    """把“3期”“三期”“第3期”统一成整数期号；无法解析时返回 None。"""
    text = str(phase or "").strip()
    if text.endswith("期"):
        text = text[:-1]
    if text.startswith("第"):
        text = text[1:]
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return _chinese_to_number(text)


def canonical_name_parts(name: str) -> tuple[str, Optional[int]]:
    """返回 (规范化主名, 期号)，期号把“3期”与“三期”视为同一期。"""
    base_name, phase = split_phase(name)
    return normalize_name(base_name), phase_to_number(phase)


def paren_name_variants(name: str) -> tuple[str, ...]:
    """拆出“主名（副名）”括号形态的主名和副名，供名称匹配兜底使用。"""
    text = str(name or "").strip()
    match = _PAREN_SUB_NAME_RE.match(text)
    if not match:
        return ()
    main = match.group("main").strip()
    inner = match.group("inner").strip()
    variants: list[str] = []
    if main:
        variants.append(main)
    if inner and normalize_name(inner) != normalize_name(main):
        variants.append(inner)
    return tuple(variants)


def _chinese_to_number(text: str) -> Optional[int]:
    """解析一至两位中文数字，如“十一”“二十三”；含无法识别的字符时返回 None。"""
    if not text:
        return None
    if "十" in text:
        left, _, right = text.partition("十")
        if left:
            if left not in _CN_DIGIT_VALUES:
                return None
            tens = _CN_DIGIT_VALUES[left]
        else:
            tens = 1
        if right:
            if right not in _CN_DIGIT_VALUES:
                return None
            ones = _CN_DIGIT_VALUES[right]
        else:
            ones = 0
        return tens * 10 + ones
    total = 0
    for char in text:
        if char not in _CN_DIGIT_VALUES:
            return None
        total = total * 10 + _CN_DIGIT_VALUES[char]
    return total


def group_name(name: str) -> str:
    """返回去除期数后的规范化小区组名称。"""
    base_name, _ = split_phase(name)
    return normalize_name(base_name)


def phase_key(phase: Optional[str]) -> str:
    """将空期数转换成可建立唯一索引的稳定键。"""
    return normalize_name(phase or "")
