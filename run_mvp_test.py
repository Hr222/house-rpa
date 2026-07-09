# -*- coding: utf-8 -*-
"""RPA 侧 MVP 测试脚本（nodriver 版）。

严格按 15 步流程，浏览器全程不自动关闭。

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
from app.models import Listing, DealRecord

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("mvp_test")

# ===== 模拟请求端数据（实际由后端发来，这里写死模拟）=====
TEST_COMMUNITY = "绿景虹湾"
TEST_AREA_MIN = 70.0
TEST_AREA_MAX = 90.0


async def run_inquiry(browser, main_page, community_name: str,
                      area_min: float, area_max: float) -> dict:
    """第4~15步：收到请求后执行完整询价。"""
    start = time.time()
    log.info("="*60)
    log.info("[4] 收到请求: 小区=%s, 面积区间=%.1f~%.1f㎡", community_name, area_min, area_max)
    log.info("="*60)

    # 第5~13步：贝壳采集（adapter 内部完成）
    pr = await collect(browser, main_page, community_name, area_min, area_max)

    log.info("--- 采集结果 ---")
    log.info("平台状态: %s", pr.status)
    if pr.reason:
        log.info("原因: %s", pr.reason)

    if pr.status == "LOGIN_EXPIRED":
        return {"success": False, "reason": "LOGIN_EXPIRED",
                "detail": "登录态失效，需人工重新登录"}

    # 报价均值：用详情页小区均价（P_quote）
    quote_avg = pr.community_avg_price
    log.info("[9] 在售单价条数: %d", len(pr.listings))
    log.info("[11] 小区均价(P_quote): %s 元/㎡", quote_avg)

    # 第12步：成交记录按面积区间筛选 → 求均值
    # 注：adapter 已只抓单价，这里 listings/deals 都是单价列表
    deal_prices = [d.unit_price for d in pr.deals if d.unit_price is not None]
    log.info("[12] 成交单价(筛选前): %d 条", len(deal_prices))
    # 成交记录的面积筛选由 adapter 按档位完成，这里直接求均值
    deal_avg = mean_price([{"unit_price": p} for p in deal_prices]) if deal_prices else None
    log.info("[12] 成交均值(P_deal): %s 元/㎡", deal_avg)

    # 第13步：§3.6 算最终单价
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
    log.info("  总耗时:   %.1fs（不含详情页停留）", elapsed)
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
    # 第1步：启动浏览器 → 打开二手房页
    log.info("[1] 启动 Edge（nodriver 反检测）...")
    browser = await nodriver.start(
        headless=False,
        lang="zh-CN",
        browser_executable_path=BROWSER_PATH,
    )
    main_page = await browser.get(config.KE_ERSHOUFANG)
    await asyncio.sleep(2)

    # 第2步：用户登录 → 回车确认
    print("\n" + "="*50)
    print("浏览器已打开贝壳二手房页。请登录。")
    print("登录完成后，回来【按回车】。")
    print("="*50)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input, "登录完成后按回车开始...")
    log.info("[2] 登录确认，程序就绪")

    # 第3步：停在二手房页等请求（模拟）
    log.info("[3] 停在二手房页，等待请求...")
    log.info("（模拟）3秒后收到请求: 小区=%s 面积=%.0f~%.0f㎡",
             TEST_COMMUNITY, TEST_AREA_MIN, TEST_AREA_MAX)
    await asyncio.sleep(3)

    # 第4~15步：执行询价
    result = await run_inquiry(browser, main_page,
                               TEST_COMMUNITY, TEST_AREA_MIN, TEST_AREA_MAX)
    print(f"\n→ 结果: {result}")

    # 浏览器不自动关闭（固有流程）
    print("\n" + "="*50)
    print("[15] 详情页将在后台停留60s后自动关闭")
    print("浏览器保持打开（固有流程，不自动关闭）")
    print("看完了按 Ctrl+C 退出")
    print("="*50)
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    browser.stop()
    log.info("测试结束")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("用户中断")
