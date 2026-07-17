# -*- coding: utf-8 -*-
"""单条测试：海岸环庆大厦，验证泛搜索检测是否生效。用完即删。"""

import time, sys, requests

BASE = "http://127.0.0.1:8000"
CITY = "深圳"
COMMUNITY = "海岸环庆大厦"
AREA = 183.59

r = requests.get(f"{BASE}/health/ready")
if r.status_code != 200:
    print("服务未就绪")
    sys.exit(1)

r = requests.post(f"{BASE}/inquiries", json={"city": CITY, "communityName": COMMUNITY, "area": AREA})
if r.status_code != 202:
    print(f"失败: {r.status_code} {r.text[:200]}")
    sys.exit(1)

tid = r.json()["data"]["taskId"]
print(f"taskId={tid[:12]}... ", end="", flush=True)

for _ in range(60):
    time.sleep(5)
    r = requests.get(f"{BASE}/inquiries/{tid}")
    if r.status_code == 429:
        time.sleep(r.json().get("data", {}).get("retryAfter", 10))
        continue
    body = r.json().get("data", {})
    if "finalPrice" in body:
        print(f"\n结果: finalPrice={body['finalPrice']}")
        break
    code = body.get("statusCode", "")
    if code == "FAILED":
        print(f"\n失败: {body}")
        break
    print(".", end="", flush=True)
