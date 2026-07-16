# -*- coding: utf-8 -*-
"""安居客平台固有常量。

安居客城市站规则：https://{城市全拼}.anjuke.com/sale/
与贝壳的拼音缩写（sz/sh/bj）不同，安居客用全拼。
当前默认深圳。
"""

START_URL = "https://shenzhen.anjuke.com/sale/"

# 安居客面积筛选档位因城市而异，不再硬编码。
# 改为运行时从结果页 HTML 动态读取（见 parsers/ajk.py:parse_area_segments）。
