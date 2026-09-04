# -*- coding: utf-8 -*-
"""房天下平台固有常量。

房天下二手房：https://{城市拼音缩写}.esf.fang.com/
与贝壳的 sz.ke.com 不同，房天下用 esf 子域。
当前默认深圳。
"""

START_URL = "https://sz.esf.fang.com/"

# 软风控（2026-09-04 自 fang_community_page_mvp 提升为平台正式规则）：
# 实测正常"打开挂牌页→内容可解析"约 3~8s（搜索式 15~18s）；
# 软风控停滞时页面可访问、无验证码，但内容迟迟不渲染（实测 60~116s）。
# 超过该秒数判定软风控：会话级恢复（回首页再直达）一次，仍超时按失败返回。
FANG_SOFT_BLOCK_SECONDS = 45.0

# 房天下面积筛选档位因城市而异，不再硬编码。
# 改为运行时从结果页 HTML 动态读取（见 parsers/fang.py:parse_area_segments）。
