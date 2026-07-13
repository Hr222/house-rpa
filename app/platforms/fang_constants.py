# -*- coding: utf-8 -*-
"""房天下平台固有常量。

房天下二手房：https://{城市拼音缩写}.esf.fang.com/
与贝壳的 sz.ke.com 不同，房天下用 esf 子域。
当前默认深圳。
"""

START_URL = "https://sz.esf.fang.com/"

# 房天下面积筛选是自定义输入框（填值+点确定），不是预设档位，
# 因此没有贝壳那种 AREA_SEGMENTS 档位映射。
