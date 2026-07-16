# -*- coding: utf-8 -*-
"""房天下平台固有常量。

房天下二手房：https://{城市拼音缩写}.esf.fang.com/
与贝壳的 sz.ke.com 不同，房天下用 esf 子域。
当前默认深圳。
"""

START_URL = "https://sz.esf.fang.com/"

# 房天下面积筛选档位因城市而异，不再硬编码。
# 改为运行时从结果页 HTML 动态读取（见 parsers/fang.py:parse_area_segments）。
