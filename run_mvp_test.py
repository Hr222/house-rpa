# -*- coding: utf-8 -*-
"""RPA 侧模拟测试脚本（nodriver 版）。

固定测试数据，零交互：启动 → 人工登录 → 回车 → 自动跑完 → 打印结果。

用法：
  python run_mvp_test.py
"""
import asyncio
import logging
import time

import nodriver

import config
from app.ke_adapter import collect, BROWSER_PATH
from app.algorithm import filter_by_area, mean_price, decide_final_price

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("mvp_test")

# ===== 固定测试数据（改这里就能换小区测）=====
TEST_COMMUNITY = "绿景虹湾"
TEST_AREA_MIN = 70.0
TEST_AREA_MAX = 90.0


async def run_inquiry(browser, community_name: str,
                      area_min: float, area_max: float) -> dict:
    """执行一次完整询价，返回结果 dict。"""
    start = time.time()
    log.info("="*60)
    log.info("询价: 小区=%s, 面积区间=%.1f~%.1f㎡", community_name, area_min, area_max)
    log.info("="*60)

    # Step 1: 贝壳采集
    pr = await collect(browser, community_name, area_min, area_max)

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

    # 成交均值：成交记录按面积区间筛选后求均值
    deal_items = [{"unit_price": d.unit_price, "area": d.area} for d in pr.deals]
    log.info("成交记录总条数(筛选前): %d", len(deal_items))
    filtered_deals = filter_by_area(deal_items, area_min, area_max)
    log.info("成交记录筛选后(%.0f~%.0f㎡): %d 条", area_min, area_max, len(filtered_deals))
    for i, d in enumerate(filtered_deals, 1):
        log.info("  成交%d: 单价=%s元/㎡ 面积=%s㎡", i, d["unit_price"], d["area"])
    deal_avg = mean_price(filtered_deals)
    log.info("成交均价(P_deal): %s 元/㎡", deal_avg)

    # §3.6 取舍
    decision = decide_final_price(quote_avg, deal_avg,
                                  config.DEAL_DIFF_THRESHOLD,
                                  config.DISCOUNT_WHEN_NO_DEAL)
    elapsed = time.time() - start

    branch_desc = {
        "TAKE_LOWER": "报价与成交差≤10%，取低",
        "DEAL_ONLY": "报价与成交差>10%，只取成交价" if quote_avg else "无报价，取成交价",
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


async def main():
    # 1. 启动 Edge（nodriver，反检测）
    log.info("启动 Edge（nodriver 反检测模式）...")
    browser = await nodriver.start(
        headless=False,
        lang="zh-CN",
        browser_executable_path=BROWSER_PATH,
    )

    # 打开贝壳首页
    tab = await browser.get(config.KE_HOME)
    await asyncio.sleep(2)

    print("\n" + "="*50)
    print(f"浏览器已打开贝壳。请在浏览器中完成登录。")
    print(f"登录完成后，回到这里【按回车】开始测试。")
    print(f"测试小区: {TEST_COMMUNITY} | 面积: {TEST_AREA_MIN}~{TEST_AREA_MAX}㎡")
    print("="*50)

    # 2. 等待人工登录——回车即继续
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input, "登录完成后按回车开始...")

    log.info("登录确认，开始测试")

    # 3. 自动跑（固定数据，不交互）
    result = await run_inquiry(browser, TEST_COMMUNITY, TEST_AREA_MIN, TEST_AREA_MAX)
    print(f"\n→ 结果: {result}")

    print("\n测试完成，浏览器保持打开供观察。按回车退出。")
    await loop.run_in_executor(None, input, "按回车退出...")
    browser.stop()
    log.info("测试结束")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("用户中断")
