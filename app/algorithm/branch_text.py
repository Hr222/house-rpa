# -*- coding: utf-8 -*-
"""Display text for algorithm decision branches."""

from __future__ import annotations


BRANCH_TEXT = {
    "NO_DATA": "无可用数据",
    "NO_MATCHING_AREA": "无匹配面积房源",
    "FAILED": "无可用结果",
    "WEIGHTED_MEDIAN": "主要价格落点中位数折扣",
    "WEIGHTED_MEDIAN_MULTI": "多个高频价格落点，取最低价格峰中位数，不打折",
    "WEIGHTED_MEDIAN_COMBINED": "挂牌价与成交价等权平均",
}
