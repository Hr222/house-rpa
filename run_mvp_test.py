# -*- coding: utf-8 -*-
"""RPA 侧模拟测试脚本（不走后端接口）。

流程：
  1. 打开浏览器 → 人工登录贝壳（手机验证码）
  2. 登录确认后，输入 {小区名, 面积}
  3. 自动执行：搜小区 → 筛面积 → 抓报价 → 进详情页抓均价+成交 → 算最终单价
  4. 打印完整结果

用法：
  python run_mvp_test.py
"""
import logging
import time

from playwright.sync_api import sync_playwright

import config
from app.ke_adapter import collect
from app.algorithm import filter_by_area, mean_price, decide_final_price

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("mvp_test")


def run_inquiry(page, community_name: str, area: float) -> dict:
    """执行一次完整询价，返回结果 dict。"""
    start = time.time()
    log.info("="*60)
    log.info("询价: 小区=%s, 基准面积=%.1f㎡", community_name, area)
    log.info("="*60)

    # Step 1: 贝壳采集
    pr = collect(page, community_name, area)

    log.info("--- 采集结果 ---")
    log.info("平台状态: %s", pr.status)
    if pr.reason:
        log.info("原因: %s", pr.reason)

    # 掉登录直接返回
    if pr.status == "LOGIN_EXPIRED":
        return {"success": False, "reason": "LOGIN_EXPIRED",
                "detail": "登录态失效，需人工重新登录"}

    # 报价均值：用详情页小区均价（用户拍板）
    quote_avg = pr.community_avg_price
    log.info("小区均价(报价P_quote): %s 元/㎡", quote_avg)
    log.info("在售房源条数: %d", len(pr.listings))

    # 成交均值：成交记录按面积±20%筛选后求均值
    deal_items = [{"unit_price": d.unit_price, "area": d.area} for d in pr.deals]
    log.info("成交记录总条数(筛选前): %d", len(deal_items))
    filtered_deals = filter_by_area(deal_items, area, config.AREA_TOLERANCE)
    log.info("成交记录筛选后(±%d%%): %d 条", int(config.AREA_TOLERANCE * 100),
             len(filtered_deals))
    for i, d in enumerate(filtered_deals, 1):
        log.info("  成交%d: 单价=%s元/㎡ 面积=%s㎡", i, d["unit_price"], d["area"])
    deal_avg = mean_price(filtered_deals)
    log.info("成交均价(P_deal): %s 元/㎡", deal_avg)

    # §3.6 取舍
    decision = decide_final_price(quote_avg, deal_avg,
                                  config.DEAL_DIFF_THRESHOLD,
                                  config.DISCOUNT_WHEN_NO_DEAL)
    elapsed = time.time() - start

    # 算法分支中文说明
    branch_desc = {
        "TAKE_LOWER": f"报价与成交差≤10%，取低",
        "DEAL_ONLY": f"报价与成交差>10%，只取成交价" if quote_avg else "无报价，取成交价",
        "QUOTE_DISCOUNT": "无成交记录，取报价×0.8",
        "FAILED": "无报价无成交，失败",
    }

    log.info("="*60)
    log.info("【最终结果】")
    log.info("  最终单价: %s 元/㎡", decision.final_price)
    log.info("  算法分支: %s (%s)", decision.branch,
             branch_desc.get(decision.branch, ""))
    log.info("  报价均值: %s 元/㎡", quote_avg)
    log.info("  成交均值: %s 元/㎡", deal_avg)
    if quote_avg and deal_avg:
        diff = abs(quote_avg - deal_avg) / deal_avg * 100
        log.info("  差异率:   %.2f%%", diff)
    log.info("  总耗时:   %.1fs", elapsed)
    log.info("="*60)

    return {
        "success": decision.branch != "FAILED",
        "final_price": decision.final_price,
        "branch": decision.branch,
        "quote_avg": quote_avg,
        "deal_avg": deal_avg,
        "elapsed": elapsed,
    }


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()

        # 1. 打开贝壳，人工登录
        log.info("打开贝壳首页，等待人工登录...")
        page.goto(config.KE_HOME, wait_until="domcontentloaded")

        print("\n" + "="*50)
        print("请在浏览器中完成贝壳登录（手机验证码）")
        print("登录完成后，回到这里输入 yes 继续")
        print("="*50)
        while True:
            ans = input("已登录？(yes): ").strip().lower()
            if ans == "yes":
                break

        log.info("登录确认，进入询价循环")

        # 2. 循环询价（可多次测试）
        while True:
            print("\n" + "-"*50)
            name = input("小区名（输入 quit 退出）: ").strip()
            if name.lower() == "quit":
                break
            area_input = input("基准面积(㎡，如 85): ").strip()
            try:
                area = float(area_input)
            except ValueError:
                print("面积格式错误，跳过")
                continue

            # 3. 执行询价
            result = run_inquiry(page, name, area)
            print(f"\n→ 结果: {result}")

        browser.close()
        log.info("测试结束")


if __name__ == "__main__":
    main()
