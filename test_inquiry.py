# -*- coding: utf-8 -*-
"""接单测试：发 POST → 等待完成后查一次结果。"""

import time
import requests

BASE = "http://127.0.0.1:8000"
COMMUNITY = "绿景虹湾"
AREA_MIN = 70
AREA_MAX = 90

r = requests.get(f"{BASE}/health/ready")
if r.status_code != 200:
    print("服务未就绪，请先登录确认所有平台")
    exit(1)
print(f"[就绪] OK")

r = requests.post(
    f"{BASE}/inquiries",
    json={"communityName": COMMUNITY, "areaMin": AREA_MIN, "areaMax": AREA_MAX},
)
task_id = r.json()["data"]["taskId"]
print(f"[接单] taskId={task_id}")
print(f"[等待采集完成] 观察浏览器...")

# 等待采集完成（可随时 Ctrl+C，结果已持久化在服务端）
for i in range(60):
    time.sleep(5)
    r = requests.get(f"{BASE}/inquiries/{task_id}")
    data = r.json()
    status = data["data"]["status"]
    if status in ("COMPLETED", "FAILED"):
        result = data["data"].get("result", {})
        print(f"\n状态: {status}")
        print(f"在售均价: {result.get('quoteAvg')}")
        print(f"成交均价: {result.get('dealAvg')}")
        print(f"最终取值: {result.get('finalPrice')}")
        print(f"决策分支: {result.get('branch')}")
        break
    if i % 6 == 0:  # 每30秒汇报一次
        print(f"[{i*5}s] 进行中...")
