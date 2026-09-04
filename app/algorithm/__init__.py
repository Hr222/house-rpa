# -*- coding: utf-8 -*-
"""房产询价算法模块：房源筛选、去重与加权落点中位数取值决策。

负责：成交/挂牌记录筛选（deal_screening 为唯一口径来源）、保守房源
去重、面积弱参考、价格峰与最终取值决策；全部为纯计算，无网络 IO。
不负责：浏览器操作、平台采集、询价编排与任务持久化。

对外入口：evaluate_algorithm，以及加权中位数折扣、弱面积容差等运行
参数读写。
内部：weighted_median.py 加权落点中位数算法 | selection.py 房源筛选
与弱引用 | listing_dedup.py 房源去重 | deal_screening.py 成交筛选 |
area_rules.py 面积区间 | branch_text.py 决策分支展示文本 |
models.py 输入输出模型 | config.py 运行参数
"""

from app.algorithm.config import (
    get_weighted_median_discount,
    get_weak_area_max_tolerance,
    is_weighted_median_discount_default,
    set_weighted_median_discount,
)
from app.algorithm.weighted_median import evaluate_algorithm

__all__ = [
    "evaluate_algorithm",
    "get_weighted_median_discount",
    "get_weak_area_max_tolerance",
    "is_weighted_median_discount_default",
    "set_weighted_median_discount",
]
