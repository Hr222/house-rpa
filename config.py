# -*- coding: utf-8 -*-
"""MVP 配置。敏感项(账号)不在此文件，登录走人工。"""

# 贝壳深圳站
KE_HOME = "https://sz.ke.com/"
KE_CITY = "sz"

# 风控规避参数
DETAIL_TAB_LINGER_SECONDS = 60     # 详情页标签停留时长（模拟真人浏览）
KEEPALIVE_INTERVAL_SECONDS = 600   # 保活间隔（10 分钟）
REQUEST_TIMEOUT_SECONDS = 60       # 单次询价超时

# 算法参数（需求 §3.6）
AREA_TOLERANCE = 0.20              # 面积 ±20%
DISCOUNT_WHEN_NO_DEAL = 0.8        # 无成交时报价 8 折
DEAL_DIFF_THRESHOLD = 0.10         # 报价/成交差异 10% 阈值
