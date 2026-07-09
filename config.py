# -*- coding: utf-8 -*-
"""RPA 询价服务配置。"""

# ===== 浏览器 =====
BROWSER_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
KE_ERSHOUFANG = "https://sz.ke.com/ershoufang/"   # 二手房页（常驻目标页）

# ===== 风控规避 =====
DETAIL_TAB_LINGER_SECONDS = 60   # 详情页标签停留时长（模拟真人浏览）
REQUEST_TIMEOUT = 30             # 单步操作超时(秒)

# ===== 算法（需求 §3.6）=====
DEAL_DIFF_THRESHOLD = 0.10       # 报价/成交差异 10% 阈值
NO_DEAL_DISCOUNT = 0.8           # 无成交时报价 × 0.8

# ===== 贝壳面积档位 (上限㎡, URL段) =====
# a1=50以下 a2=50-70 a3=70-90 a4=90-110 a5=110-140 a6=140-170 a7=170以上
AREA_SEGMENTS = [
    (50, "a1"), (70, "a2"), (90, "a3"), (110, "a4"),
    (140, "a5"), (170, "a6"), (9999, "a7"),
]
