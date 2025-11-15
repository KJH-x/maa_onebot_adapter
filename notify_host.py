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

            main_logger.info(f"WebSocket (@{port_config['ws']}) 和 HTTP (@{port_config['http']}) 服务器已启动")
            await asyncio.Future()


if __name__ == "__main__":
    main_logger = setup_logging(log_level=logging.INFO)
    main_logger.info('[HOST] (I) 日志系统已初始化')
    local_config = load_config()
    main_logger.info('[HOST] (I) 已读取配置文件')

    try:
        asyncio.run(main(local_config,{'ws': 8765, 'http': 8080}))
    except KeyboardInterrupt:
        main_logger.info("❌ Signal Interrupt (Ctrl+C) -> 退出")
