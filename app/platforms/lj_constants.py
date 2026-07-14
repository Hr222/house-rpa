# -*- coding: utf-8 -*-
"""链家平台固有常量。

链家二手房：https://{城市拼音缩写}.lianjia.com/ershoufang/
链家是贝壳的子公司，DOM 和贝壳高度一致（同代码库），但搜索/面积筛选/成交记录有差异。
当前默认深圳。
"""

START_URL = "https://sz.lianjia.com/ershoufang/"

# 链家面积筛选是自定义输入框：
# 需先点"更多选项"展开筛选区 → 找"面积"区 → 点"更多及自定义"展开 customFilter → 填 min-max → 确定
# 因此没有贝壳那种 AREA_SEGMENTS 预设档位映射。
