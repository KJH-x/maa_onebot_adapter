import argparse
import asyncio
import logging
from typing import Any

import websockets

from file_handler import load_config, setup_logging
from web_server import (
    AioHttpServerWrapper,
    WebSocketServer,
    create_http_app,
    process_request,
)


async def main(config: dict[str, Any], port_config: dict[str, int], host: str = "0.0.0.0"):

    server = WebSocketServer(config)
    http_server = create_http_app('/maaReport')

    async with websockets.serve(server.handler, host, port_config["ws"],
                                process_request=process_request):
        async with AioHttpServerWrapper(http_server, host, port_config["http"]):

            main_logger.info(f"[ WS Server][ StartUp] WebSocket (@{port_config['ws']}) 和 HTTP (@{port_config['http']}) 服务器已启动")
            await asyncio.Future()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行 WebSocket 和 HTTP 服务器。")
    parser.add_argument(
        "--loglevel",
        "-l",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="设置日志级别 (debug, info, warning, error)",
    )
    args = parser.parse_args()
    input_log_level:str = args.loglevel.lower()

    log_level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    selected_log_level = log_level_map.get(input_log_level, logging.INFO)

    main_logger = setup_logging(log_level=selected_log_level)
    main_logger.info(f'[ Launcher ][Log:Init] 日志系统已初始化, Loglevel = {input_log_level.upper()}')
    local_config = load_config()
    main_logger.info('[ Launcher ][Cfg:Load] 已读取配置文件')

    try:
        asyncio.run(main(local_config,{'ws': 8765, 'http': 8080}))
    except KeyboardInterrupt:
        main_logger.info("❌ Signal Interrupt (Ctrl+C) -> 退出")
