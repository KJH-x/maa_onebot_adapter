import asyncio
import json
import logging
import time
from types import TracebackType
from typing import Any, Optional, Type

import aiohttp
from aiohttp import web
from aiohttp.web_request import Request
from file_handler import load_config, write_config
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

logger = logging.getLogger('app')


class WebSocketServer:
    def __init__(self, config: dict[str, Any]):
        # 保底，不应直接使用
        self.config = config

        # 对话白名单
        react_info: list[dict[str, str | int]] = config.get("reaction_to_sender", "")
        self.active_group = [_.get("group_id", "") for _ in react_info if _.get("message_type", "") == "group"]
        self.active_private = [_.get("user_id", "") for _ in react_info if _.get("message_type", "") == "private"]
        logger.debug(f"groups:{self.active_group}, privates:{self.active_private}")

        # 发信目标服务器
        http_info: dict[str, str] = config.get("http_server_info", "")
        self.http_uri: str = http_info.get("http", "")
        logger.debug(f"http target: {self.http_uri}")

        # ws 客户端列表
        self.client_info: list[dict[str, Any]] = config.get("client_info", "")
        # 构建 UA->token 映射，便于快速查找
        self.ua_token_map = {info.get("UA", ""): info.get("Bearer", {}).get("token", "") for info in self.client_info}
        logger.debug(f"ua_token_map: {self.ua_token_map}")

        # 本地状态缓存
        # self.maa_reports_cache: list[dict[str, Any]] = []
        self.maa_reports_cache: dict[str, Any] = load_config("cache.json")

        # 用户映射
        self.user_map: dict[str, dict[str, Any]] = config.get("user_map", {})
        self.log_to: dict[str, Any] = config.get("log_to", {})

    def get_message_text(self, message_dict: dict[str, Any], lower: bool = True):
        chat_message_struct: list[dict[str, Any]] = message_dict.get("message", {})
        chat_message_text = ""
        for structure in chat_message_struct:
            if structure.get("type", "") == "text":
                _data: dict[str, str] = structure.get("data", {})
                chat_message_text = _data.get("text", "").strip()
        if lower:
            return chat_message_text.lower()
        else:
            return chat_message_text

    async def construct_reply(
        self,
            message_dict: dict[str, Any],
            reply_message: list[str] = [""],
            at: Optional[int] = None
    ):
        data: dict[str, Any] = {}
        structured_message: list[Any] = []
        message_type = message_dict.get("message_type", "")
        user_id: str = message_dict.get("user_id", "")
        group_id: str = message_dict.get("group_id", "")

        # 发信类型 群组/私聊
        if message_type == "group":
            data.update({"group_id": f"{group_id}"})
        elif message_type == "private":
            data.update({"user_id": f"{user_id}"})
        else:
            logger.error("incoming msg type is neither group or private")

        # @user_id 会在消息最前端
        if at and message_type == "group":
            structured_message.append({
                "type": "at",
                "data": {"qq": f"{at}"}
            })

        # 拼接加入所有消息
        for msg in reply_message:
            structured_message.append({
                "type": "text",
                "data": {"text": f"{msg}"}
            })
        data.update({"message": structured_message})
        return data

    async def send_dict_to_client(self, websocket: ServerConnection, data_to_send: dict[str, Any], api_path: str = ""):
        """
        ## 方法暂时不可用
        检查接收到的message是否为空，如果不为空，则将data_to_send字典
        序列化为JSON字符串并通过websocket发送给客户端。

        参数:
        - websocket: 当前的websocket连接对象。
        - message: 从客户端接收到的原始消息（用于判断是否为空）。
        - data_to_send: 要发送给客户端的Python字典。
        """
        try:
            # 改造消息
            response_payload = data_to_send.copy()
            if api_path:
                response_payload['action'] = api_path.strip("/")
            else:
                return
            json_message = json.dumps(response_payload)

            # 发送消息
            await websocket.send(json_message)
            logger.info(" (S) 发送消息")

        except TypeError:
            logger.error("❌ 错误：待发送数据无法序列化为JSON。请确保传入的是字典。")
        except (ConnectionClosedError, ConnectionClosedOK):
            logger.error("🔌 警告：发送时连接已关闭，消息未能送达。")
        except Exception as e:
            logger.error(f"❓ 未知错误：发送消息时发生意外错误。{e}")

    async def send_dict_to_api(self, data_to_send: dict[str, Any], api_path: str = "") -> Optional[dict[str, Any]]:
        """
        ## 这应该是一个过渡方案，但是目前ws通信不可用，还不能切换
        向指定的URL(API端点)发送POST请求。

        参数:
        - url: 目标API的基础URL。
        - data_to_send: 要发送的字典数据。
        - api_path: 可选的API路径，会附加到url后面。

        返回:
        - 如果请求成功，返回解析后的JSON响应字典。
        """
        try:
            if not data_to_send:
                logger.info("⚠️ 警告：发送数据为空，已取消请求。")
                return None

            # 构造请求 URL/header
            # 没有适配https，因为这应该是一个过渡方案，最终应该使用ws通信，期间也不想升级为https
            full_url = f"{self.http_uri.rstrip('/')}/{api_path.lstrip('/')}" if api_path else self.http_uri
            headers = {"Content-Type": "application/json"}

            # 发送请求
            async with aiohttp.ClientSession() as session:
                async with session.post(full_url, headers=headers, json=data_to_send) as response:
                    if response.status == 200:
                        logger.info(f"[HTTP] (S) 已成功向 http 服务器 {api_path} 发送POST请求")
                        logger.debug(f"[HTTP] (S) 向({full_url}) POST ({data_to_send})")
                        try:
                            return await response.json()

                        except aiohttp.ContentTypeError:
                            # 返回非JSON响应
                            text_resp = await response.text()
                            logger.error("⚠️ 返回的不是JSON格式：", text_resp)
                            return {"raw_response": text_resp}
                    else:
                        logger.error(f"❌ 请求失败，HTTP状态码: {response.status}")
                        text_resp = await response.text()
                        logger.error("📩 响应内容：", text_resp)
                        return None

        except aiohttp.ClientConnectionError:
            logger.error("🔌 错误：无法连接到服务器，请检查网络或URL是否正确。")
        except aiohttp.InvalidURL:
            logger.error("❌ 错误：提供的URL无效。")
        except Exception as e:
            logger.error(f"❓ 未知错误：发送POST请求时发生意外错误。{e}")

        return None

    async def async_http_request_with_retry(
        self, url: str, data: dict[str, Any], max_retries: int = 3
    ) -> Optional[aiohttp.ClientResponse]:
        """
        使用aiohttp实现带有重试和指数退避机制的异步HTTP POST请求。

        参数:
            url: 请求的目标URL。
            data: 作为JSON载荷发送的数据字典。
            max_retries: 最大重试次数。

        返回:
            成功时的aiohttp.ClientResponse对象，失败时的None。
        """
        headers = {
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = data.copy()
        retries = 0

        # 使用ClientSession来管理连接
        async with aiohttp.ClientSession(headers=headers) as session:
            while retries <= max_retries:
                response = None
                try:
                    # POST使用json参数自动序列化数据
                    async with session.post(url, json=payload) as response:
                        response.raise_for_status()

                        return await response.json()

                except aiohttp.ClientResponseError as e:
                    # 处理HTTP状态码错误 (4xx, 5xx)
                    logger.warning(f"HTTP错误状态码：{e.status}")
                    if response is not None:
                        # 获取响应内容进行打印（需要使用await）
                        response_text = await response.text()
                        logger.info(f"响应内容：{response_text}")

                    if retries < max_retries:
                        retries += 1
                        wait_time = 2 ** retries
                        logger.warning(f"重试 ({retries}/{max_retries})... 暂停 {wait_time} 秒")
                        await asyncio.sleep(wait_time)  # 异步暂停
                    else:
                        logger.error(f"达到最大重试次数 ({max_retries})，放弃。")
                        return None

                except aiohttp.ClientConnectorError as e:
                    # 处理连接错误（如DNS解析失败、连接被拒绝等）
                    logger.info(f"连接错误：{e}")

                    if retries < max_retries:
                        retries += 1
                        wait_time = 2 ** retries
                        logger.warning(f"重试 ({retries}/{max_retries})... 暂停 {wait_time} 秒")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"达到最大重试次数 ({max_retries})，放弃。")
                        return None

                except Exception as e:
                    logger.error(f"发生未知错误：{e}")
                    return None

    async def send(self, api_path: str, data_to_send: dict[str, Any]):
        logger.debug(f"[HTTP] (P) 准备发送: {data_to_send}")

        # (variable) message_type: Literal['group', 'private']
        response = await self.send_dict_to_api(api_path=api_path, data_to_send=data_to_send)
        if response and response.get('status', '') == 'ok':
            logger.info("[HTTP] (R) 发送成功")
        else:
            logger.info("[HTTP] (R) 发送失败")
        return response

    def check_connection(self, websocket: ServerConnection) -> tuple[bool, Optional[str]]:
        client_address = websocket.remote_address
        # 检查连接信息
        if not getattr(websocket, "request", None):
            logger.warning(f"放弃来自({client_address[0]}:{client_address[1]})的未知连接，其 request 为空")
            return False, None

        # 连接信息获取
        ws_request = websocket.request
        headers = ws_request.headers if ws_request else None
        if not headers:
            return False, None

        def get_header(name: str) -> str:
            value = headers.get(name, "")
            return value[0] if isinstance(value, list) else value
        client_user_agent = get_header("user-agent")
        client_auth = get_header("authorization").removeprefix("Bearer").strip()

        # UA校验
        if client_user_agent not in self.ua_token_map:
            logger.warning(f"放弃来自({client_address[0]}:{client_address[1]})的未知连接，其 UA 为({client_user_agent})")
            return False, None

        # Token校验
        expected_token = self.ua_token_map[client_user_agent]
        if client_auth != expected_token:
            logger.error(f"UA({client_user_agent}) 提供的认证信息不匹配，来自({client_address[0]}:{client_address[1]})")
            return False, None

        return True, client_user_agent

    async def handler(self, websocket: ServerConnection):
        """
        处理传入消息（查询）
        """
        connection = asyncio.get_running_loop()
        client_address = websocket.remote_address

        # 检查连接参数
        accept, client_type = self.check_connection(websocket)
        if not accept:
            return

        logger.info(f"[ WS ] (E) 建立来自({client_address[0]}:{client_address[1]})的({client_type})连接")

        try:
            if client_type == 'OneBot/11':
                # 处理请求会话
                async for ws_income_message in websocket:
                    # 解析传入消息
                    if isinstance(ws_income_message, bytes):
                        try:
                            message_str = ws_income_message.decode('utf-8')
                        except UnicodeDecodeError:
                            logger.warning("⚠️ 警告：无法以UTF-8解码字节串消息，跳过。")
                            continue
                    else:
                        message_str = str(ws_income_message)

                    try:
                        # 解析JSON
                        message_dict: dict[str, Any] = json.loads(message_str)
                        message_type = message_dict.get("message_type", "")
                        user_id: int = message_dict.get("user_id", "")
                        group_id: int = message_dict.get("group_id", "")

                        # 筛选对话白名单
                        if (group_id in self.active_group and message_type == "group") or\
                                (user_id in self.active_private and message_type == "private"):
                            chat_message_text = self.get_message_text(message_dict)

                            # 筛选前缀提示词
                            if chat_message_text.startswith("maa"):
                                logger.info(f"[ WS ] (R) 收到 {chat_message_text}")
                                chat_command = chat_message_text.removeprefix("maa").strip()

                                if chat_command in ["help", ""]:
                                    reply_data = await self.construct_reply(
                                        message_dict=message_dict, reply_message=[
                                            "提示词MAA, 大小写通用\n",
                                            "命令/别名 功能描述\n",
                                            "help （空）显示本帮助信息\n",
                                            "测试/test 测试回复状态，返回值为[“测试收到”]\n",
                                            "ws状态/ws 查询控制器的Web Socket连接状态\n",
                                            "现在/currentuser/... 查询当前正在执行的配置用户\n",
                                            "下一个/nextuser/... 查询MAA在当前配置执行完成后，即将执行的下一配置用户\n",
                                            "控制器/host/... 查询MAA和控制器当前的状态\n",
                                            "report 返回一个包含所有关键状态信息的详细报告报告内容\n",
                                        ]
                                    )

                                # 处理命令
                                elif chat_command in ["测试", "test"]:
                                    reply_data = await self.construct_reply(
                                        message_dict=message_dict, reply_message=["测试收到"]
                                    )

                                elif chat_command in ["ws状态", "ws"]:
                                    try:
                                        pong = await websocket.ping()
                                        latency = await pong

                                        reply_data = await self.construct_reply(
                                            message_dict=message_dict,
                                            reply_message=[" MAA 控制器的 web socket 连接状态为：",
                                                           self.maa_reports_cache["Connection"],
                                                           f"延迟{latency}s"]
                                        )
                                    except ConnectionClosed:
                                        reply_data = await self.construct_reply(
                                            message_dict=message_dict,
                                            reply_message=[" MAA 控制器的 web socket 连接已断开"]
                                        )
                                        self.maa_reports_cache.update({"Connection": "Unreachable"})
                                        write_config(data=self.maa_reports_cache, config_path="cache.json")

                                elif chat_command in ["现在", "当前", "currentuser", "current"]:
                                    reply_data = await self.construct_reply(
                                        message_dict=message_dict,
                                        reply_message=[" MAA 当前正在执行 ",
                                                       self.maa_reports_cache["CurruentUser"],
                                                       " 的配置"]
                                    )

                                elif chat_command in ["下一个", "即将", "nextuser", "next"]:
                                    reply_data = await self.construct_reply(
                                        message_dict=message_dict,
                                        reply_message=[" MAA 当前正在执行 ",
                                                       self.maa_reports_cache["NextUser"],
                                                       " 的配置"]
                                    )

                                elif chat_command in ["host", "控制器", "controller", "next", "status"]:
                                    reply_data = await self.construct_reply(
                                        message_dict=message_dict,
                                        reply_message=[" MAA 和控制器当前的状态为",
                                                       self.maa_reports_cache["Status"]]
                                    )

                                elif chat_command in ["report"]:
                                    reply_data = await self.construct_reply(
                                        message_dict=message_dict,
                                        reply_message=[f"当前配置: {self.maa_reports_cache["CurruentUser"]}\n",
                                                       f"下一配置: {self.maa_reports_cache["NextUser"]}\n",
                                                       f"ws连接: {self.maa_reports_cache["Connection"]}\n",
                                                       f"控制器状态: {self.maa_reports_cache["Status"]}\n",]
                                    )
                                else:
                                    continue

                                _ = await self.send(api_path=f"send_{message_type}_msg", data_to_send=reply_data)

                    except json.JSONDecodeError as e:
                        logger.error(f"❌ 错误：JSON解析失败！原始消息不是合法的JSON格式。")
                        logger.error(f"   错误详情：{e}")
                    except Exception as e:
                        logger.error(f"❓ 未知错误：处理消息时发生意外错误。{e}")

            elif client_type == 'MaaReport/00':
                # 处理汇报会话
                # start_time 会在 starting 部分 覆盖，此处保证变量绑定
                start_time = time.time()
                async for ws_income_message in websocket:
                    # 解析传入消息
                    if isinstance(ws_income_message, bytes):
                        try:
                            message_str = ws_income_message.decode('utf-8')
                        except UnicodeDecodeError:
                            logger.warning("⚠️ 警告：无法以UTF-8解码字节串消息，跳过。")
                            continue
                    else:
                        message_str = str(ws_income_message)

                    try:
                        # 解析并补充JSON
                        message_dict: dict[str, Any] = json.loads(message_str)
                        logger.info(f"[ WS ] (R) 收到来自({client_type})的汇报")
                        logger.debug(f"[ WS ] (R) ({client_type})汇报内容{{message_dict}}")
                        message_dict.update({"lastUpdate": time.time()})

                        # 上一个Update的信息缓存
                        last_user: str = self.maa_reports_cache["CurruentUser"]
                        last_update: float = self.maa_reports_cache["lastUpdate"]
                        duration = (time.time()-last_update)/60

                        # 更新本地缓存
                        self.maa_reports_cache.update(message_dict)
                        notify_message_list: list[str] = []

                        # 如果汇报有配置才提醒
                        user: str = message_dict.get("CurruentUser", "")
                        TotalSteps: str = message_dict.get("TotalSteps", "")
                        if (update_status := message_dict.get("Status", "")) == "GotoNext":
                            # 查本地表
                            user_id: int = self.user_map[user].get("user_id", "")
                            group_id: int = self.user_map[user].get("group_id", "")
                            message_type: str = self.user_map[user].get("message_type", "")

                            # 通用通知, @会被置于句首,应当将提醒放在句首
                            message_dict.update(self.user_map[user])
                            notify_message_list.append(f" 即将开始运行MAA一键长草（20s），请注意\n")

                            # 特别地，群聊发送时，获取上一个人信息
                            if message_type == "group" and last_user:
                                _api_response = await self.send("get_group_member_info", {
                                    "group_id": group_id,
                                    "user_id": last_user,
                                    "no_cache": False
                                })
                                if _api_response:
                                    _api_data: dict[str, Any] | None = _api_response.get("data", {})
                                    if _api_data:
                                        last_user_card: str = _api_data.get("card", "")
                                        notify_message_list.append(f"{last_user_card}已完成,耗时{duration:.2f}分钟\n\n")
                                    else:
                                        # -> json{data:null} -> pyDict{data:None} 群里没这人
                                        notify_message_list.append(f"上一配置已完成,耗时{duration:.2f}分钟\n\n")

                            # 群内 @user_id, 私聊因为 message_dict 中的 message_type 为 private. 不会将@加入
                            notify_data = await self.construct_reply(
                                message_dict=message_dict,
                                reply_message=notify_message_list,
                                at=user_id
                            )
                            _ = await self.send(api_path=f"send_{message_type}_msg", data_to_send=notify_data)

                            write_config(data=self.maa_reports_cache, config_path="cache.json")

                        elif update_status == "Starting":
                            start_time = time.time()

                            # message_type 的获取方法和上述不同
                            message_type: str = self.log_to.get("message_type", "")
                            notify_data = await self.construct_reply(
                                message_dict=self.log_to,
                                reply_message=[
                                    f"MAA 即将在 60s 内开始挂机第一个账号, 总共有 {TotalSteps} 个账号等待挂机"]
                            )
                            _ = await self.send(api_path=f"send_{message_type}_msg", data_to_send=notify_data)

                        elif update_status == "Finished":
                            # message_type 的获取方法和上述不同
                            message_type: str = self.log_to.get("message_type", "")
                            notify_data = await self.construct_reply(
                                message_dict=self.log_to,
                                reply_message=[
                                    f"MAA 已完成挂机 {TotalSteps} 个账号, 耗时 {(time.time()-start_time)/60:.3f} 分钟"]
                            )
                            _ = await self.send(api_path=f"send_{message_type}_msg", data_to_send=notify_data)

                    except json.JSONDecodeError as e:
                        logger.error(f"❌ 错误：JSON解析失败！原始消息不是合法的JSON格式。")
                        logger.error(f"   错误详情：{e}")
                    except Exception as e:
                        logger.error(f"❓ 未知错误：处理消息时发生意外错误。{e}")

        except ConnectionClosedError as e:
            logger.error(f"🔌 连接异常断开: {client_address}。错误码/状态：{e.code}。")
        except ConnectionClosedOK as e:
            logger.error(f"👋 连接正常关闭: {client_address}。")
        except Exception as e:
            logger.error(f"🚨 顶级错误：处理连接时发生致命错误。{e}")

        finally:
            logger.info(f"🛑 连接处理结束: {client_address}")
            connection.close()


class AioHttpServerWrapper:
    def __init__(self, app: web.Application, host: str, port: int):
        self.app = app
        self.host = host
        self.port = port
        self.runner = None
        self.site = None

    async def __aenter__(self):
        """进入上下文时执行服务器启动逻辑。"""
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


def create_http_app(api_path: str = "/apipath"):
    app = web.Application()

    async def http_handler(request: Request):
        client_info = request.remote or "Unknown"

        logger.info(f"收到来自 ({client_info}) 的 ({request.method}) 请求 ({api_path})")
        if request.method == 'POST' and request.content_type =='application/json':
            try:
                post_content = await request.json()
                # post_content = json.loads(content.decode('utf-8'))
                logger.info(f"内容可被解析为 {post_content}")
            except json.JSONDecodeError:
                logger.warning("⚠️ 警告：请求体不是有效的JSON格式。")
                return web.Response(status=400, text="Bad Request: Invalid JSON body")
            except UnicodeDecodeError:
                logger.warning("⚠️ 警告：无法以UTF-8解码请求体。")
                return web.Response(status=400, text="Bad Request: Failed to decode request body")
            except Exception as e:
                # 捕获其他可能的读取错误
                logger.error(f"读取请求体时发生错误: {e}")
                return web.Response(status=500, text="Internal Server Error during body reading")
                    
                # 在这里处理解析后的 post_content (dict/list)
                # 例如：return web.json_response({"status": "ok", "data": post_content})

        elif request.method == 'POST' and request.content_type == 'text/plain':
            # 如果是纯文本POST请求
            post_content = await request.text()
            logger.info(f"内容成功解析为文本: {post_content}")
        else:
            logger.info(f"不支持的请求内容类型: ({request.content_type})")
            post_content = ""
        return web.Response(status=200, text="OK")
    
    async def undefined_path_handler(request: Request):
        client_info = request.remote or "Unknown"
        logger.info(f"收到来自 ({client_info}) 的无效请求 ({request.path})")
        return web.Response(status=404, text=f"{request.path} not found.")

    app.add_routes([
        web.get(api_path, http_handler),
        web.post(api_path,http_handler),
        web.route('*', '/{tail:.*}', undefined_path_handler)
        ])
    return app
