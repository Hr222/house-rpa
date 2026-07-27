# -*- coding: utf-8 -*-
"""FastAPI 服务启动入口。"""

from __future__ import annotations

import argparse
import socket

import uvicorn

from app.core import config
from app.api import create_app
from app.utils.debug_utils import set_debug_mode
from app.utils.logging_utils import setup_logging
from app.runtime import RPARuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug",
        "--excel",
        dest="debug",
        action="store_true",
        help="开启 RPA 调试模式，导出关键页面 HTML 到 excel 目录（兼容旧参数 --excel）。",
    )
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="启用终端回车确认登录：平台未就绪时提示回车，人工完成登录后继续。",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.API_PORT,
        help=f"API 监听端口（默认 {config.API_PORT}）。",
    )
    return parser


def _ensure_port_available(host: str, port: int) -> None:
    """在启动浏览器前检查监听端口，避免端口冲突产生半启动状态。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(
            f"API 端口 {host}:{port} 已被占用，请先停止占用进程或使用 --port 指定其他端口"
        ) from exc
    finally:
        probe.close()


def main():
    args = build_parser().parse_args()
    if args.debug:
        set_debug_mode(True)

    setup_logging()
    _ensure_port_available(config.API_HOST, args.port)
    runtime = RPARuntime(enable_console_ready_confirmation=args.manual_login)
    app = create_app(runtime=runtime, manage_runtime=True)
    uvicorn.run(
        app,
        host=config.API_HOST,
        port=args.port,
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
