# -*- coding: utf-8 -*-
"""钉钉机器人通知验证脚本。

独立运行，不走 RPA 主流程，只验证钉钉 webhook 是否可用。

用法::

    # 方式1：设环境变量
    set DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
    python -m app.scripts.dingtalk_test

    # 方式2：命令行传参（优先于环境变量）
    python -m app.scripts.dingtalk_test https://oapi.dingtalk.com/robot/send?access_token=xxx

    # 方式3：用 uvicorn 同一环境运行
    python -m app.scripts.dingtalk_test --url https://oapi.dingtalk.com/robot/send?access_token=xxx

注意：钉钉机器人安全设置如果选了"自定义关键词"，
消息内容必须包含该关键词才能发送成功（建议关键词设为"风控"或"RPA"）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# 确保项目根目录在 sys.path 中（直接运行脚本时需要）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.utils.dingtalk import send_text, send_markdown


async def main(webhook_url: str | None = None):
    """发送测试消息验证钉钉机器人。

    Args:
        webhook_url: 钉钉 webhook 地址，为 None 则读环境变量。
    """
    # 如果命令行传了 URL，临时覆盖环境变量（让 config 模块读到）
    if webhook_url:
        os.environ["DINGTALK_WEBHOOK_URL"] = webhook_url
        # config 模块在导入时已读取环境变量，需要重新加载
        from app.core import config
        config.DINGTALK_WEBHOOK_URL = webhook_url

    from app.core import config
    webhook = config.DINGTALK_WEBHOOK_URL
    if not webhook:
        print("❌ 未配置 DINGTALK_WEBHOOK_URL")
        print("   方式1: set DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx")
        print("   方式2: python -m app.scripts.dingtalk_test <webhook_url>")
        return

    # 脱敏显示（只显示前60字符）
    display = webhook[:60] + "..." if len(webhook) > 60 else webhook
    print(f"webhook: {display}")
    print()

    # 1. 测试纯文本
    print("--- 发送纯文本消息 ---")
    ok1 = await send_text("【RPA测试】这是一条测试消息，验证钉钉机器人通知是否正常。风控")
    print(f"结果: {'✅ 成功' if ok1 else '❌ 失败'}")
    print()

    await asyncio.sleep(1)  # 避免发太快被限流

    # 2. 测试 markdown
    print("--- 发送 markdown 消息 ---")
    ok2 = await send_markdown(
        "RPA风控告警(测试)",
        "## RPA 通知测试\n\n"
        "- **平台**: 贝壳\n"
        "- **事件**: 验证码拦截(测试)\n"
        "- **页码**: 第3页\n"
        "- **状态**: 等待人工处理\n\n"
        "> 此为测试消息，验证钉钉机器人通知功能是否正常工作。\n\n"
        "风控",
    )
    print(f"结果: {'✅ 成功' if ok2 else '❌ 失败'}")
    print()

    if ok1 and ok2:
        print("✅ 钉钉机器人验证通过，可对接风控流程")
    else:
        print("❌ 部分消息发送失败，请检查:")
        print("   1. webhook URL 是否正确")
        print("   2. 机器人安全设置：关键词是否包含'风控'或'RPA'")
        print("   3. 网络是否能访问 oapi.dingtalk.com")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="钉钉机器人通知验证")
    parser.add_argument("url", nargs="?", default=None,
                        help="钉钉 webhook URL（不传则读环境变量 DINGTALK_WEBHOOK_URL）")
    parser.add_argument("--url", dest="url_opt", default=None,
                        help="钉钉 webhook URL（等价于位置参数）")
    args = parser.parse_args()
    url = args.url or args.url_opt
    asyncio.run(main(url))
