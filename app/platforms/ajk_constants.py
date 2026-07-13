# -*- coding: utf-8 -*-
"""安居客平台固有常量。

安居客城市站规则：https://{城市全拼}.anjuke.com/sale/
与贝壳的拼音缩写（sz/sh/bj）不同，安居客用全拼。
当前默认深圳。
"""

START_URL = "https://shenzhen.anjuke.com/sale/"

# 安居客面积筛选是自定义输入框（填值+点确定），不是预设档位，
# 因此没有贝壳那种 AREA_SEGMENTS 档位映射。
