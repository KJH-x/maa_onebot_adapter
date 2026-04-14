"""
HTTP服务器模块 - Phase2重构
基于接口定义从web_server.py拆分出的独立模块
"""

import asyncio
import json
import logging
from http import HTTPStatus
from typing import Dict, List, Callable, Any, Optional, Type
from types import TracebackType

from aiohttp import web
from aiohttp.web_request import Request as WebRequest
from websockets.asyncio.server import ServerConnection
from websockets.http11 import Request as WS11Request

from src.logging_system import get_web_server_logger, log_exception

# 类型定义导入
try:
    from src.type_definitions import (
        ConfigDict,
        DifyResponse,
        EventPayload,
    )
except ImportError:
    # 回退类型定义
    from typing import TypedDict
    
    class ConfigDict(TypedDict):
        dify_api_key: str
        dify_base_url: str
        port: int
        host: str
        log_level: str
        log_file: Optional[str]
        max_log_size: int
        log_backup_count: int
    
    DifyResponse = Dict[str, Any]
    EventPayload = Dict[str, Any]

logger = logging.getLogger('app')


class RouteInfo:
    """HTTP路由信息"""
    method: str
    path: str
    handler_name: str


class HttpServer:
    """HTTP API服务器接口"""
    
    def __init__(self, config: ConfigDict):
        """初始化HTTP服务器"""
        self.config = config
        self.app = web.Application()
    
    async def start(self, host: str = "0.0.0.0", port: int = 8766) -> None:
        """启动HTTP服务器"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"HTTP服务器已启动在 {host}:{port}")
    
    async def stop(self) -> None:
        """停止HTTP服务器"""
        # 注意：实际实现需要保存runner引用
        pass
    
    def add_route(self, method: str, path: str, handler: Callable) -> None:
        """添加HTTP路由"""
        self.app.router.add_route(method, path, handler)
    
    def add_static_route(self, path: str, directory: str) -> None:
        """添加静态文件路由"""
        self.app.router.add_static(path, directory)
    
    def get_routes(self) -> List[RouteInfo]:
        """获取所有路由信息"""
        routes = []
        for route in self.app.router.routes():
            info = RouteInfo()
            info.method = route.method
            info.path = str(route.path)
            info.handler_name = route.handler.__name__
            routes.append(info)
        return routes
    
    # 向后兼容的接口
    async def handle_maa_report(self, request: web.Request) -> web.Response:
        """处理MAA报告请求（保持现有接口）"""
        # 这里实现MAA报告处理逻辑
        return web.Response(status=200, text="MAA Report OK")
    
    async def handle_dify_webhook(self, request: web.Request) -> web.Response:
        """处理Dify Webhook请求（保持现有接口）"""
        # 这里实现Dify Webhook处理逻辑
        return web.Response(status=200, text="Dify Webhook OK")


class AioHttpServerWrapper:
    """HTTP服务器包装器（向后兼容）"""
    
    def __init__(self, app: web.Application, host: str, port: int):
        self.app = app
        self.host = host
        self.port = port
        self.runner = None
        self.site = None

    async def __aenter__(self):
        """进入上下文时执行服务器启动逻辑"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        # 返回 runner 或 site 对象，通常不需要，但为了形式一致可以返回
        return self.site

    async def __aexit__(
        self, exc_type: Optional[Type[BaseException]],
            exc_val: Optional[BaseException], exc_tb: Optional[TracebackType]) -> bool:
        """退出上下文时执行服务器关闭和资源清理。"""
        if self.runner:
            logger.info("HTTP 服务器正在关闭...")
            await self.runner.cleanup()
        # 返回 False 允许异常继续传播
        return False


def create_http_app(api_path: str = "/apipath") -> web.Application:
    """
    创建HTTP应用（向后兼容）
    
    Args:
        api_path: API路径，默认为"/apipath"
    
    Returns:
        web.Application: aiohttp Web应用实例
    """
    app = web.Application()

    async def http_handler(request: WebRequest):
        client_info = request.remote or "Unknown"

        logger.info(f"收到来自 ({client_info}) 的 ({request.method}) 请求 ({api_path})")
        if request.method == 'POST' and request.content_type == 'application/json':
            try:
                post_content = await request.json()
                logger.info(f"内容可被解析为 {post_content}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}")
                return web.Response(status=400, text="Bad Request: Invalid JSON body")
            except UnicodeDecodeError:
                logger.warning("⚠️ 警告：无法以UTF-8解码请求体。")
                return web.Response(status=400, text="Bad Request: Failed to decode request body")
            except Exception as e:
                # 捕获其他可能的读取错误
                log_exception(logger, e, "读取请求体时发生错误")
                return web.Response(status=500, text="Internal Server Error during body reading")

        elif request.method == 'POST' and request.content_type == 'text/plain':
            # 如果是纯文本POST请求
            post_content = await request.text()
            logger.info(f"内容成功解析为文本: {post_content}")
        else:
            logger.info(f"不支持的请求内容类型: ({request.content_type})")
            post_content = ""
        return web.Response(status=200, text="OK")

    async def undefined_path_handler(request: WebRequest):
        client_info = request.remote or "Unknown"
        logger.info(f"收到来自 ({client_info}) 的无效请求 ({request.path})")
        return web.Response(status=404, text=f"{request.path} not found.")

    app.add_routes([
        web.get(api_path, http_handler),
        web.post(api_path, http_handler),
        web.route('*', '/{tail:.*}', undefined_path_handler)
    ])
    return app


async def process_request(connection: ServerConnection, request: WS11Request) -> None:
    """
    为了解决以下来自 asyncio.wait 的报错 尝试使用此函数, 但暂未成功, 根源来自Napcat的Websocket在重连之前发送了非握手ws消息（非法数据）
    
    相关讨论: [fastapi/fastapi/"opening handshake failed" for websocket endpoint #8388](https://github.com/fastapi/fastapi/discussions/8388)
    
    完整的报错信息:
    ```
        opening handshake failed
    Traceback (most recent call last):
    File "/miniconda3/Lib/site-packages/websockets/http11.py", line 138, in parse
        request_line = yield from parse_line(read_line)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    File "/miniconda3/Lib/site-packages/websockets/http11.py", line 309, in parse_line
        line = yield from read_line(MAX_LINE_LENGTH)
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    File "/miniconda3/Lib/site-packages/websockets/streams.py", line 46, in read_line
        raise EOFError(f"stream ends after {p} bytes, before end of line")
    EOFError: stream ends after 0 bytes, before end of line

    The above exception was the direct cause of the following exception:

    Traceback (most recent call last):
    File "/miniconda3/Lib/site-packages/websockets/server.py", line 545, in parse
        request = yield from Request.parse(
                ^^^^^^^^^^^^^^^^^^^^^^^^^
            self.reader.read_line,
            ^^^^^^^^^^^^^^^^^^^^^^
        )
        ^
    File "/miniconda3/Lib/site-packages/websockets/http11.py", line 140, in parse
        raise EOFError("connection closed while reading HTTP request line") from exc
    EOFError: connection closed while reading HTTP request line

    The above exception was the direct cause of the following exception:

    Traceback (most recent call last):
    File "/miniconda3/Lib/site-packages/websockets/asyncio/server.py", line 356, in conn_handler
        await connection.handshake(
        ...<3 lines>...
        )
    File "/miniconda3/Lib/site-packages/websockets/asyncio.server.py", line 207, in handshake
        raise self.protocol.handshake_exc
    websockets.exceptions.InvalidMessage: did not receive a valid HTTP request
    ```
    """
    connection_hdr = request.headers.get("Connection", "").lower()
    peername = connection.transport.get_extra_info("peername")

    if "upgrade" not in connection_hdr:
        logger.warning(f"[ WS ] 来自 ({peername or None}) 的 HTTP 握手错误, 返回 400 BAD_REQUEST")
        return connection.respond(HTTPStatus.BAD_REQUEST, text="upgrade not in connection")

    # logger.warning(f"[ WS ] ({connection.subprotocol})")
    # -> [ WS ] (None)
    return None  # None = 继续 WebSocket 握手