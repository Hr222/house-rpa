# -*- coding: utf-8 -*-
"""算法决策分支的展示文本。"""

from __future__ import annotations


BRANCH_TEXT = {
    "NO_DATA": "无可用数据",
    "NO_MATCHING_AREA": "无匹配面积房源",
    "FAILED": "无可用结果",
    "WEIGHTED_MEDIAN": "主要价格落点中位数折扣",
    "WEIGHTED_MEDIAN_MULTI": "多个高频价格落点，取最低价格峰中位数，不打折",
    "WEIGHTED_MEDIAN_COMBINED": "挂牌价与成交价等权平均",
    # ===== 可比市场算法（market_comparison，DEFAULT）=====
    "MARKET_DEAL_AVG": "可比成交样本充足（≥3），取近半年同面积成交均价",
    "MARKET_LISTING_CONSISTENT": "成交样本不足，报价与成交差距≤15%，采用报价频率最高价",
    "MARKET_DEAL_ANCHORED": "成交样本不足且与报价差距>15%，成交均价上浮10%",
    "MARKET_NEARBY_DEAL_ANCHORED": "本小区无成交，周边成交均值高于报价，均值上浮10%",
    "MARKET_NEARBY_DEAL_LOWER": "本小区无成交，周边成交均值不高于报价，采用报价频率最高价",
    "MARKET_LISTING_AVERAGE": "目标与周边小区均无成交，取各小区报价频率最高价均值",
}
