# -*- coding: utf-8 -*-
"""RPA 询价 MVP 测试脚本。

基于 nodriver 官方用法：用 uc.loop() 启动，Element API 操作。
固定测试数据，登录后回车即跑，浏览器全程不关闭。

用法：
  python run_mvp_test.py
"""
import asyncio
import logging

import nodriver as uc

import config
from app.ke_adapter import collect
from app.algorithm import mean, decide
from app.models import PlatformResult

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mvp")

# ===== 固定测试数据（实际由后端发来）=====
TEST_COMMUNITY = "绿景虹湾"
TEST_AREA_MIN = 70.0
TEST_AREA_MAX = 90.0


async def run_inquiry(browser, main_page):
    """第4~14步：收到请求后执行完整询价。"""
    # 第4~13步：采集（adapter 内部完成步骤5~13）
    pr: PlatformResult = await collect(
        browser, main_page, TEST_COMMUNITY, TEST_AREA_MIN, TEST_AREA_MAX)

    log.info("--- 采集结果 ---")
    log.info("状态: %s  原因: %s", pr.status, pr.reason or "无")

    if pr.status == "LOGIN_EXPIRED":
        return

    # P_quote：详情页小区均价（用户拍板）
    quote_avg = pr.community_avg_price
    # 若详情页没均价，用搜索到的在售单价均值兜底
    if not quote_avg and pr.quote_prices:
        quote_avg = mean(pr.quote_prices)

    # P_deal：成交单价均值（adapter已只抓单价）
    deal_avg = mean(pr.deal_prices) if pr.deal_prices else None

    log.info("在售单价: %d 条", len(pr.quote_prices))
    log.info("成交单价: %d 条", len(pr.deal_prices))
    log.info("报价均值(P_quote): %s 元/㎡", quote_avg)
    log.info("成交均值(P_deal): %s 元/㎡", deal_avg)

    # 第13步：§3.6 算最终单价
    d = decide(quote_avg, deal_avg,
               config.DEAL_DIFF_THRESHOLD, config.NO_DEAL_DISCOUNT)

    branch_desc = {
        "TAKE_LOWER": "差≤10%取低",
        "DEAL_ONLY": "差>10%只取成交" if quote_avg else "无报价取成交",
        "QUOTE_DISCOUNT": "无成交取报价×0.8",
        "FAILED": "无报价无成交",
    }

    log.info("="*50)
    log.info("【最终结果】")
    log.info("  最终单价: %s 元/㎡", d.final_price)
    log.info("  算法分支: %s (%s)", d.branch, branch_desc.get(d.branch, ""))
    if quote_avg and deal_avg:
        log.info("  差异率: %.2f%%", abs(quote_avg - deal_avg) / deal_avg * 100)
    log.info("="*50)

    # 第14步：返回结果（打印，实际场景返回给后端）
    print(f"\n→ 最终单价: {d.final_price} 元/㎡ | 分支: {d.branch}")
    print(f"  报价均值: {quote_avg} | 成交均值: {deal_avg}\n")


async def main():
    # 第1步：启动浏览器（nodriver 官方用法）
    log.info("[1] 启动 Edge（nodriver 反检测）")
    browser = await uc.start(
        headless=False,
        browser_executable_path=config.BROWSER_PATH,
        lang="zh-CN",
    )

    # 打开二手房页（目标页，不是首页）
    main_page = await browser.get(config.KE_ERSHOUFANG)
    await main_page
    await uc.sleep(2)

    # 第2步：用户登录 → 回车确认
    print("\n" + "="*50)
    print("浏览器已打开贝壳二手房页。请登录。")
    print("登录完成后，回来按回车。")
    print(f"测试: {TEST_COMMUNITY} {TEST_AREA_MIN}~{TEST_AREA_MAX}㎡")
    print("="*50)
    input("登录后按回车开始...")
    log.info("[2] 登录确认")

    # 第3步：停在二手房页等请求（模拟3秒后收到）
    log.info("[3] 就绪，3秒后模拟收到请求")
    await uc.sleep(3)

    # 第4~14步：执行询价
    await run_inquiry(browser, main_page)

    # 浏览器不自动关闭（固有流程）
    print("="*50)
    print("[15] 详情页后台停留60s自动关闭")
    print("浏览器保持打开。Ctrl+C 退出。")
    print("="*50)
    try:
        while True:
            await uc.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    browser.stop()
    log.info("测试结束")


if __name__ == "__main__":
    import asyncio
    uc.loop().run_until_complete(main())
