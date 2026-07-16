# -*- coding: utf-8 -*-
"""接单测试：发 POST → 等待完成后查一次结果。"""

import time
import requests

BASE = "http://127.0.0.1:8000"
COMMUNITY = "绿景虹湾"
AREA = 89.5

r = requests.get(f"{BASE}/health/ready")
if r.status_code != 200:
    print("服务未就绪，请先登录确认所有平台")
    exit(1)
print(f"[就绪] OK")

r = requests.post(
    f"{BASE}/inquiries",
    json={"communityName": COMMUNITY, "area": AREA},
)
task_id = r.json()["data"]["taskId"]
print(f"[接单] taskId={task_id}")
print(f"[等待采集完成] 观察浏览器...")

# 等待采集完成（可随时 Ctrl+C，结果已持久化在服务端）
for i in range(60):
    time.sleep(5)
    r = requests.get(f"{BASE}/inquiries/{task_id}")
    resp = r.json()
    body = resp.get("data", {})

    # 有 finalPrice 说明已完成
    if "finalPrice" in body:
        print(f"\n状态: COMPLETED")
        print(f"在售均价: {body.get('quoteAvg')}")
        print(f"成交均价: {body.get('dealAvg')}")
        print(f"最终取值: {body.get('finalPrice')}")
        print(f"决策分支: {body.get('branch')}")
        break

    status = body.get("statusCode") or body.get("status", "")
    if status in ("FAILED",):
        print(f"\n状态: FAILED — {body}")
        break

    if i % 6 == 0:  # 每30秒汇报一次
        print(f"[{i*5}s] {status}...")
