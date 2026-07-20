# -*- coding: utf-8 -*-
"""房产评估对比测试：逐条调用 RPA 询价接口，比较询价结果与评估单价的偏差。

用法：
  1. 先启动 RPA 服务并确认所有平台就绪：
     python -m app.scripts.api_server --debug --manual-login
  2. 再跑本脚本：
     python test_evaluate.py

输出：results/评估对比_{timestamp}.xlsx
"""

import time
import sys
import requests
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# ─── 配置 ──────────────────────────────────────────────
BASE_URL = "http://127.0.0.1:8000"
INPUT_FILE = "C:/Users/Administrator/Desktop/房产评估汇总表_问题行测试.xlsx"
OUTPUT_DIR = Path(__file__).parent / "results"
POLL_INTERVAL = 6       # 轮询间隔秒数（>5 避免连续 429）
MAX_WAIT = 600          # 单任务最长等待秒数（10 分钟，fang 最多翻 10 页约 70s）
DEFAULT_CITY = "广州"    # Excel 无 city 列时的默认城市

# ─── 读取评估表 ─────────────────────────────────────────
wb_in = openpyxl.load_workbook(INPUT_FILE)
ws_in = wb_in.active

# 读取表头，建立列名→列号映射
col_map = {}
for c in range(1, ws_in.max_column + 1):
    h = ws_in.cell(row=1, column=c).value
    if h:
        col_map[str(h).strip()] = c

city_col = col_map.get("city")
area_col = col_map.get("面积㎡", 1)
price_col = col_map.get("评估单价", 2)
community_col = col_map.get("小区名称", 4)
last_data_col = max(col_map.values()) if col_map else 4
out_start_col = last_data_col + 1  # 对比列起始位置

if city_col:
    print(f"[city] 检测到 city 列（第 {city_col} 列），将逐行读取城市")
else:
    print(f"[city] 未检测到 city 列，使用默认城市: {DEFAULT_CITY}")

# 表头列名: city? | 面积㎡ | 评估单价 | 房产评估总值 | 小区名称 | ...
data = []
for row_idx in range(2, ws_in.max_row + 1):
    community = ws_in.cell(row=row_idx, column=community_col).value
    area = ws_in.cell(row=row_idx, column=area_col).value
    eval_price = ws_in.cell(row=row_idx, column=price_col).value
    if not community or not area or not eval_price:
        continue
    city = None
    if city_col:
        city = ws_in.cell(row=row_idx, column=city_col).value
        city = str(city).strip() if city else ""
    if not city:
        city = DEFAULT_CITY
    data.append({
        "city": city,
        "community": str(community).strip(),
        "area": float(area),
        "eval_price": float(eval_price),
    })

print(f"读取到 {len(data)} 条评估记录")

# ─── 检查服务就绪 ───────────────────────────────────────
r = requests.get(f"{BASE_URL}/health/ready")
if r.status_code != 200:
    print("❌ RPA 服务未就绪，请先启动并确认所有平台就绪")
    sys.exit(1)
print("[就绪] 服务 OK")

# ─── 逐条询价 ───────────────────────────────────────────
results = []

for i, item in enumerate(data):
    community = item["community"]
    area = item["area"]
    eval_price = item["eval_price"]
    city = item["city"]

    print(f"\n[{i+1}/{len(data)}] {city} {community} 面积={area}㎡ 评估单价={eval_price}")

    # 创建询价任务（503 时等待后重试）
    for retry in range(6):
        r = requests.post(
            f"{BASE_URL}/inquiries",
            json={"city": city, "communityName": community, "area": area, "algorithmMode": "default"},
        )
        if r.status_code == 202:
            break
        if r.status_code == 503:
            print(f"  服务降级中，10s后重试({retry+1}/6)...", flush=True)
            time.sleep(10)
        else:
            break

    if r.status_code != 202:
        print(f"  ❌ 创建任务失败: {r.status_code} {r.text[:100]}")
        results.append({
            "社区": community, "面积": area, "评估单价": eval_price,
            "询价单价": None, "差距%": None, "分支": "ERROR",
            "在售均价": None, "成交均价": None, "状态": "FAILED",
        })
        continue

    task_id = r.json()["data"]["taskId"]
    print(f"  taskId={task_id[:12]}... 等待中", end="", flush=True)

    # 轮询结果
    elapsed = 0
    final_data = None
    while elapsed < MAX_WAIT:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        r = requests.get(f"{BASE_URL}/inquiries/{task_id}")

        # 限流 429：等提示的秒数再试
        if r.status_code == 429:
            retry = r.json().get("data", {}).get("retryAfter", 10)
            time.sleep(retry)
            elapsed += retry
            continue

        body = r.json().get("data", {})

        # task 已完成且有 finalPrice → 正常取值
        if "finalPrice" in body and body["finalPrice"] is not None:
            final_data = body
            print(f"  完成 ({elapsed}s)")
            break

        # task 已完成但没 finalPrice（全平台 NO_DATA，branch=NO_DATA）
        status_code = body.get("statusCode", body.get("status", ""))
        if status_code in ("COMPLETED", "FAILED"):
            # 优先显示各平台无数据原因汇总（note），其次分支，最后 error
            note = body.get("note") or body.get("branch") or body.get("error", "无数据")
            final_data = body
            print(f"  无数据 ({elapsed}s): {note}")
            break

        print(".", end="", flush=True)

    if final_data is None:
        results.append({
            "社区": community, "面积": area, "评估单价": eval_price,
            "询价单价": None, "差距%": None, "分支": "TIMEOUT",
            "在售均价": None, "成交均价": None, "状态": "TIMEOUT",
        })
        continue

    # 计算结果
    final_price = final_data.get("finalPrice")
    branch = final_data.get("branchCode", final_data.get("branch", ""))
    quote_avg = final_data.get("quoteAvg")
    deal_avg = final_data.get("dealAvg")

    if final_price is None:
        results.append({
            "社区": community, "面积": area, "评估单价": eval_price,
            "询价单价": None, "差距%": None, "分支": branch or "NO_DATA",
            "在售均价": quote_avg, "成交均价": deal_avg, "状态": "全部平台无数据",
        })
        continue

    diff_pct = None
    if final_price and eval_price:
        diff_pct = round((final_price - eval_price) / eval_price * 100, 2)

    # 判断分支含义
    if "QUOTE" in str(branch):
        branch_display = "采用售均价"
    elif "DEAL" in str(branch):
        branch_display = "差值>10%，取成交均价"
    elif "TAKE_LOWER" in str(branch):
        branch_display = "差值≤10%，取较低值"
    elif branch == "FAILED":
        branch_display = "无数据"
    else:
        branch_display = str(branch)

    print(f"  询价={final_price} | 评估={eval_price} | 差距={diff_pct}% | {branch_display}")

    results.append({
        "社区": community,
        "面积": area,
        "评估单价": eval_price,
        "询价单价": final_price,
        "差距%": diff_pct,
        "分支": branch_display,
        "在售均价": quote_avg,
        "成交均价": deal_avg,
        "状态": "OK",
    })

# ─── 输出 Excel（基于原表追加对比列）─────────────────────
OUTPUT_DIR.mkdir(exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out_path = OUTPUT_DIR / f"评估对比_{timestamp}.xlsx"

# 复制原表
wb_in = openpyxl.load_workbook(INPUT_FILE)
ws = wb_in.active

# 在原表右侧追加对比表头
header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
header_font = Font(bold=True, size=11, color="FFFFFF")
thin_border = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)

add_headers = ["询价单价", "差距比例%", "偏差评级", "是否采用售均价"]
for j, h in enumerate(add_headers):
    cell = ws.cell(row=1, column=out_start_col + j, value=h)
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = Alignment(horizontal="center")
    cell.border = thin_border

# 补数据
for i, r in enumerate(results):
    row = i + 2

    # 询价单价
    cell = ws.cell(row=row, column=out_start_col, value=r["询价单价"])
    cell.border = thin_border
    cell.alignment = Alignment(horizontal="center")
    if r["询价单价"]:
        cell.number_format = '#,##0.00'

    # 差距比例%
    diff = r["差距%"]
    cell = ws.cell(row=row, column=out_start_col + 1, value=diff if diff is not None else "N/A")
    cell.border = thin_border
    cell.alignment = Alignment(horizontal="center")
    if diff is not None:
        cell.number_format = '0.00"%"'

    # 偏差评级
    if diff is None:
        rating = "N/A"
    elif abs(diff) <= 5:
        rating = "偏差小（≤5%）"
    elif abs(diff) <= 10:
        rating = "偏差中等（5%~10%）"
    else:
        rating = "偏差大（>10%）"
    cell = ws.cell(row=row, column=out_start_col + 2, value=rating)
    cell.border = thin_border
    cell.alignment = Alignment(horizontal="center")

    # 是否采用售均价
    cell = ws.cell(row=row, column=out_start_col + 3, value=r["分支"])
    cell.border = thin_border
    cell.alignment = Alignment(horizontal="center")

# 列宽
from openpyxl.utils import get_column_letter
for j, width in enumerate([14, 14, 16, 28]):
    col_letter = get_column_letter(out_start_col + j)
    ws.column_dimensions[col_letter].width = width

# 汇总行
summary_row = len(results) + 3
summary_col = community_col
ws.cell(row=summary_row, column=summary_col, value="汇总").font = Font(bold=True)

valid_diffs = [r["差距%"] for r in results if r["差距%"] is not None]
if valid_diffs:
    avg_diff = sum(valid_diffs) / len(valid_diffs)
    max_diff = max(valid_diffs)
    min_diff = min(valid_diffs)
    within_10 = sum(1 for d in valid_diffs if abs(d) <= 10)
    ws.cell(row=summary_row, column=out_start_col, value=f"有效: {len(valid_diffs)}/{len(results)} 条")
    ws.cell(row=summary_row + 1, column=out_start_col, value=f"平均偏差: {avg_diff:.2f}%")
    ws.cell(row=summary_row + 2, column=out_start_col, value=f"最大偏差: {max_diff:.2f}%")
    ws.cell(row=summary_row + 3, column=out_start_col, value=f"最小偏差: {min_diff:.2f}%")
    ws.cell(row=summary_row + 4, column=out_start_col, value=f"偏差≤10%: {within_10}/{len(valid_diffs)} 条")

wb_in.save(out_path)
print(f"\n✅ 结果已保存: {out_path}")
