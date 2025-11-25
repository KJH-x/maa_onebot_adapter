import asyncio
import json
import logging
import time
from http import HTTPStatus
from types import TracebackType
from typing import Any, Optional, Type

# import aiohttp
import uuid6
from aiohttp import web
from aiohttp.web_request import Request as WebRequest
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)
from websockets.http11 import Request as WS11Request

from file_handler import load_config, write_config
from llm_parse import parse_command

logger = logging.getLogger('app')


class WebSocketServer:
    def __init__(self, config: dict[str, Any]):
        # 保底，不应直接使用
        self.config = config

        # 对话白名单
        react_info: list[dict[str, str | int]] = config.get("reaction_to_sender", "")
        self.active_group = [_.get("group_id", "") for _ in react_info if _.get("message_type", "") == "group"]
        self.active_private = [_.get("user_id", "") for _ in react_info if _.get("message_type", "") == "private"]
        logger.debug(f"[ WS Server][CfgParse] groups:{self.active_group}, privates:{self.active_private}")

        # websocket 客户端列表
        self.client_info: list[dict[str, str]] = config.get("client_info", "")
        # 构建 UA->token 映射，便于快速查找
        self.ua_token_map = {info.get("UA", ""): info.get("token", "") for info in self.client_info}
        logger.debug(f"[ WS Server][CfgParse] ua_token_map: {self.ua_token_map}")

        # 本地状态缓存
        # self.maa_reports_cache: list[dict[str, Any]] = []
        self.maa_reports_cache: dict[str, Any] = load_config("cache.json")

        # 用户映射
        self.user_map: dict[str, dict[str, Any]] = config.get("user_map", {})
        self.reverse_user_map: dict[int, str] = {
            user_data["user_id"]: config_name
            for config_name, user_data in self.user_map.items()
        }
        logger.debug(f"[ WS Server][CfgParse] self.reverse_user_map {self.reverse_user_map}")

        self.msg_route: dict[str, Any] = config.get("msg_route", {})

        # 会话 WS 集
        # self.clients: dict[str,] = {'OneBot/11': [], 'MaaCtrl/00': []}
        self.OneBotClients:  Optional[ServerConnection] = None
        self.MaaCtrlClients:  Optional[ServerConnection] = None

        # 消息等待池
        self.waiting_pool: dict[str, asyncio.Future[Any]] = {}

        # 大模型这一块
        self.llm:dict[str,str] = config.get("external_llm",{})
        self.gemini_key:str = self.llm.get("gemini","")

        # END
        logger.info("[ WS Server][CfgParse] Config File loaded")

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


    async def make_websocket_msg(
        self,
            original_msg: dict[str, Any],
            reply_text: list[str] = [],
            at: Optional[int] = None,
            attach_image: list[tuple[str,str]] = []
    ) -> dict[str, Any]:
        websocket_data: dict[str, Any] = {}
        inner_data: dict[str, Any] = {}
        structured_message: list[Any] = []
        message_type = original_msg.get("message_type", "")
        user_id: str = original_msg.get("user_id", "")
        group_id: str = original_msg.get("group_id", "")

        # 发信类型 群组/私聊
        if message_type == "group":
            websocket_data.update({"action": "send_group_msg"})
            inner_data.update({"group_id": f"{group_id}"})
        elif message_type == "private":
            websocket_data.update({"action": "send_private_msg"})
            inner_data.update({"user_id": f"{user_id}"})
        else:
            logger.error("[ Msg/mkMsg][MsgT:ERR] original message type is neither group or private")
            raise ValueError(f"original message type {original_msg} is neither group or private")

        # @user_id 会在消息最前端
        if at and message_type == "group":
            structured_message.append({
                "type": "at",
                "data": {"qq": f"{at}"}
            })

        # 拼接加入所有消息
        for msg in reply_text:
            structured_message.append({
                "type": "text",
                "data": {"text": f"{msg}"}
            })
        inner_data.update({"message": structured_message})

        # "data": {
        #     "summary": "[图片]", //截图
        #     "file": "file://D:/a.jpg" // 本地路径 or
        #     "file": "http://.....png" // 网络路径 or
        #     "file": "base64://xxxxxx" // base64编码
        # }
        for img,img_summary in attach_image:
            if img.startswith("http") or img.startswith("base64://"):
                structured_message.append({
                    "type":"image",
                    "data": {"file":img,"summary":img_summary}
                })
            else:
                logger.error("[ Msg/mkMsg][ImgT:ERR]Ignoring img with corrupted format")


        # 合并消息
        websocket_data.update({"params": inner_data})
        # 添加标识符
        websocket_data.update({"echo": str(uuid6.uuid7())})
        return websocket_data

    def check_connection(self, websocket: ServerConnection) -> tuple[bool, Optional[str]]:
        client_address = websocket.remote_address
        # 检查连接信息
        if not getattr(websocket, "request", None):
            logger.warning(f"[ CheckConn ][Req:Null] 放弃来自({client_address[0]}:{client_address[1]})的未知连接，其 request 为空")
            return False, None

        # 连接信息获取
        ws_request = websocket.request
        headers = ws_request.headers if ws_request else None
        if not headers:
            return False, None

        def _get_header(name: str) -> str:
            value = headers.get(name, "")
            return value[0] if isinstance(value, list) else value
        client_user_agent = _get_header("user-agent")
        client_auth = _get_header("authorization").removeprefix("Bearer").strip()

        # UA校验
        if client_user_agent not in self.ua_token_map:
            logger.warning(f"[ CheckConn ][UA:Unkwn] 放弃来自({client_address[0]}:{client_address[1]})的未知连接，其 UA 为({client_user_agent})")
            return False, None

        # Token校验
        expected_token = self.ua_token_map[client_user_agent]
        if client_auth != expected_token:
            logger.error(f"[ CheckConn ][AuthDeny] UA({client_user_agent}) 提供的认证信息不匹配，来自({client_address[0]}:{client_address[1]})")
            return False, None

        return True, client_user_agent

    async def _handler_OneBotClient(self):
        client_type = "OneBotClients"
        if not (websocket := self.OneBotClients):
            raise ValueError(f"{client_type} is {self.OneBotClients}")

        async for ws_income_message in websocket:
            try:
                # 解析JSON
                message_dict: dict[str, Any] = json.loads(ws_income_message)
                message_type = message_dict.get("message_type", "")
                user_id: int = message_dict.get("user_id", "")
                group_id: int = message_dict.get("group_id", "")

                # 协程消息共享
                if (msg_id := message_dict.get("echo", "")) and msg_id in self.waiting_pool:
                    future = self.waiting_pool[msg_id]
                    if not future.done():
                        future.set_result(message_dict)
                    # 不继续处理
                    continue

                # 筛选对话白名单
                if (group_id in self.active_group and message_type == "group") or\
                        (user_id in self.active_private and message_type == "private"):
                    chat_message_text = self.get_message_text(message_dict)

                    # 筛选前缀提示词
                    if chat_message_text.startswith("maa"):
                        logger.info(f"[  Msg/One ][Msg:Recv] 收到来自({client_type})的汇报")
                        logger.debug(f"[  Msg/One ][Msg:Recv] 汇报内容:\n{message_dict}\n")
                        chat_command = chat_message_text.removeprefix("maa").strip()

                        if chat_command in ["help", ""]:
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict, reply_text=[
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
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict, reply_text=["测试收到"]
                            )

                        elif chat_command in ["ws状态", "ws"]:
                            pong = await websocket.ping()
                            latency_1 = await pong
                            if self.MaaCtrlClients:
                                try:
                                    pong = await self.MaaCtrlClients.ping()
                                    latency_2 = await pong

                                    reply_data = await self.make_websocket_msg(
                                        original_msg=message_dict,
                                        reply_text=[
                                            f"🐧-{latency_1*1000:.1f}ms-🔄-{latency_2*1000:.1f}ms-<⚙>"]
                                    )
                                except ConnectionClosed:
                                    reply_data = await self.make_websocket_msg(
                                        original_msg=message_dict,
                                        reply_text=[f"🐧-{latency_1*1000:.1f}ms-🔄-⛓️‍💥-<⚙>"]
                                    )
                                    self.maa_reports_cache.update({"Connection": "Unreachable"})
                                    write_config(data=self.maa_reports_cache, config_path="cache.json")
                            else:
                                reply_data = await self.make_websocket_msg(
                                    original_msg=message_dict,
                                    reply_text=[f"🐧-{latency_1*1000:.1f}ms-🔄-⛓️‍💥-<⚙>"]
                                )
                                self.maa_reports_cache.update({"Connection": "Unreachable"})
                                write_config(data=self.maa_reports_cache, config_path="cache.json")

                        elif chat_command in ["现在", "当前", "currentuser", "current"]:
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[" MAA 当前正在执行 ",
                                            self.maa_reports_cache["CurruentUser"],
                                            " 的配置"]
                            )

                        elif chat_command in ["下一个", "即将", "nextuser", "next"]:
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[" MAA 当前正在执行 ",
                                            self.maa_reports_cache["NextUser"],
                                            " 的配置"]
                            )

                        elif chat_command in ["host", "控制器", "controller", "next", "status"]:
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[" MAA 和控制器当前的状态为",
                                            self.maa_reports_cache["Status"]]
                            )

                        elif chat_command in ["report"]:
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[f"当前配置: {self.maa_reports_cache["CurruentUser"]}\n",
                                            f"下一配置: {self.maa_reports_cache["NextUser"]}\n",
                                            f"ws连接: {self.maa_reports_cache["Connection"]}\n",
                                            f"控制器状态: {self.maa_reports_cache["Status"]}\n",]
                            )
                        else:
                            logger.debug(f"[  Msg/One ][Fwd2:llm] {chat_command}")
                            new_command =  await parse_command(self.gemini_key,chat_command)
                            new_command.update({"config":self.reverse_user_map.get(user_id,"")})
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[f"测试阶段，仅返回LLM输出: {str(new_command).replace('\'','')}"]
                            )

                        # 发送消息
                        msg_id = reply_data.get("echo", "")

                        logger.info(f'[  Msg/One ][Dat:Prep] Ready to send message {msg_id}')
                        logger.debug(f"[  Msg/One ][Dat:Prep] Detail:\n{reply_data}\n")
                        await self.OneBotClients.send(json.dumps(reply_data))

                        # 等待服务器确认
                        while True:
                            response = await self.OneBotClients.recv()
                            response_data: dict[str, Any] = json.loads(response)
                            if (echo := response_data.get("echo", "")) and echo == msg_id:
                                if response_data.get("status", "")=='ok':
                                    logger.info(f"[  Msg/One ][SendConf] Message send confirmed {msg_id}")
                                    logger.debug(f"[  Msg/One ][SendConf] Detail:\n{response_data}\n")
                                else:
                                    logger.warning(f"[  Msg/One ][SendFail] Message send FAILED {msg_id}")
                                    logger.debug(f"[  Msg/One ][SendFail] Detail:\n{response_data}\n")
                                break
                            else:
                                logger.debug(f"[  Msg/One ][ Warning] Discarding msg without echo: {response}")
                                continue

            except Exception as e:
                logger.error(f"[  Msg/One ][ Unknown] 未知错误：处理消息时发生意外错误。{e}")

    async def _handler_MaaCtrlClient(self):
        client_type = "MaaCtrlClients"
        if not (websocket := self.MaaCtrlClients):
            raise ValueError(f"{client_type} is {self.MaaCtrlClients}")
        # start_time 会在 starting 部分 覆盖，此处保证变量绑定
        start_time = time.time()
        async for ws_income_message in websocket:
            try:
                # 解析并补充JSON
                message_dict: dict[str, Any] = json.loads(ws_income_message)
                logger.info(f"[  Msg/Maa ][Msg:Recv] 收到来自({client_type})的汇报")
                logger.debug(f"[  Msg/Maa ][Msg:Recv] 汇报内容:\n{message_dict}\n")
                message_dict.update({"lastUpdate": time.time()})

                # 上一个Update的信息缓存
                last_user: str = self.maa_reports_cache["CurruentUser"]
                last_update: float = self.maa_reports_cache["lastUpdate"]
                duration = (time.time()-last_update)/60

                # 更新本地缓存
                self.maa_reports_cache.update(message_dict)
                notify_message_list: list[str] = []

                user: str = message_dict.get("CurruentUser", "")
                TotalSteps: str = message_dict.get("TotalSteps", "")

                # 个性化通知
                if (update_status := message_dict.get("Status", "")) == "Next_Step":
                    logger.debug(f"[  Msg/Maa ][Dat:Prep] Preparing At message")
                    # 查本地表
                    user_id: int = self.user_map[user].get("user_id", "")
                    group_id: int = self.user_map[user].get("group_id", "")
                    message_type: str = self.user_map[user].get("message_type", "")

                    # 通用通知, @会被置于句首,应当将提醒放在句首
                    message_dict.update(self.user_map[user])
                    notify_message_list.append(f" 即将开始运行MAA一键长草（20s），请注意\n")

                    # 特别地，群聊发送时，获取上一个人信息
                    if message_type == "group" and last_user and self.OneBotClients:

                        msg_id = str(uuid6.uuid7())
                        request_data: dict[str, Any] = {"action": "get_group_member_info", "echo": msg_id}
                        request_data.update({
                            "params": {
                                "group_id": group_id,
                                "user_id": last_user,
                                "no_cache": False
                            }
                        })
                        logger.info(f"[  Msg/Maa ][Dat:Prep] Ready to fetch user card from group")

                        # 使用协程池向OneBot询问
                        logger.info(f"[ > OneBot ][Req:Sent] Asking OneBot about user card")
                        logger.debug(f"[ > OneBot ][Req:Sent] Detail:\n{request_data}\n")
                        await self.OneBotClients.send(json.dumps(request_data))
                        future: asyncio.Future[Any] = asyncio.Future()
                        self.waiting_pool[msg_id] = future
                        _api_response:dict[str,Any] = await future

                        if _api_response:
                            logger.info(f"[ < OneBot ][Req:Back] Receiving user card")
                            logger.debug(f"[ < OneBot ][Req:Back] Detail:\n{_api_response}\n")
                            _api_data: dict[str, Any] | None = _api_response.get("data", {})
                            if _api_data:
                                last_user_card: str = _api_data.get("card", "")
                                notify_message_list.append(f"{last_user_card}已完成,耗时{duration:.2f}分钟\n\n")
                            else:
                                # -> json{data:null} -> pyDict{data:None} 群里没这人
                                notify_message_list.append(f"\n上一配置已完成,耗时{duration:.2f}分钟")

                    # 群内 @user_id, 私聊因为 message_dict 中的 message_type 为 private. 不会将@加入
                    reply_data = await self.make_websocket_msg(
                        original_msg=message_dict,
                        reply_text=notify_message_list,
                        at=user_id
                    )
                    write_config(data=self.maa_reports_cache, config_path="cache.json")

                else:
                    # 管理通知
                    # message_type 的获取方法和上述不同
                    message_type: str = self.msg_route.get("message_type", "")
                    if update_status == "Starting":
                        start_time = time.time()

                        reply_data = await self.make_websocket_msg(
                            original_msg=self.msg_route,
                            reply_text=[
                                f"MAA 即将在 60s 内开始挂机第一个账号, 总共有 {TotalSteps} 个账号等待挂机"]
                        )

                    elif update_status == "Reconnect":
                        reply_data = await self.make_websocket_msg(
                            original_msg=self.msg_route,
                            reply_text=[
                                f"MAA 控制器重连成功，当前 {TotalSteps} 个账号等待挂机"]
                        )

                    elif update_status == "Finished":
                        reply_data = await self.make_websocket_msg(
                            original_msg=self.msg_route,
                            reply_text=[
                                f"MAA 已完成挂机 {TotalSteps} 个账号, 耗时 {(time.time()-start_time)/60:.3f} 分钟"]
                        )

                    elif update_status == "ManuallyStopped":
                        reply_data = await self.make_websocket_msg(
                            original_msg=self.msg_route,
                            reply_text=[
                                f"MAA 管理器被终止"]
                        )
                    else:
                        continue

                if self.OneBotClients:
                    msg_id = reply_data.get("echo", "")
                    logger.info(f'[ > OneBot ][Send:Msg] Ready to send message {msg_id}')
                    logger.debug(f'[ > OneBot ][Send:Msg] Detail:\n{reply_data}\n')
                    await self.OneBotClients.send(json.dumps(obj=reply_data))
                    future: asyncio.Future[Any] = asyncio.Future()
                    self.waiting_pool[msg_id] = future
                    try:
                        # cannot call recv while another coroutine is already running recv or recv_streaming
                        # response = await self.OneBotClients.recv()
                        response_data = await future
                        logger.info(f"[  Msg/Maa ][SendConf] Message send confirmed {msg_id}")
                        logger.debug(f"[  Msg/Maa ][SendConf] Detail:\n{response_data}\n")
                    except Exception as e:
                        # 如果发生异常 (如连接中断, 超时等), 可以取消future
                        if msg_id in self.waiting_pool:
                            future.cancel()
                        logger.warning(f"[  Msg/Maa ][SendFail] Message send FAILED")
                        logger.debug(f"[  Msg/Maa ][SendFail] Detail:\n{e}\n")
                        raise e
                    finally:
                        # 确保清理等待池
                        if msg_id in self.waiting_pool:
                            del self.waiting_pool[msg_id]
                else:
                    logger.debug(f"[  Msg/Maa ][SendFail] self.OneBotClients is {self.OneBotClients}")

            except Exception as e:
                logger.error(f"[  Msg/Maa ][Unknown] 未知错误：处理消息时发生意外错误。{e}")

    async def handler(self, websocket: ServerConnection):
        """
        处理传入消息（查询）
        """
        _ = asyncio.get_running_loop()
        client_address = websocket.remote_address

        # 检查连接参数
        accept, client_type = self.check_connection(websocket)
        if not accept:
            await websocket.close()
            return

        logger.info(f"[ Msg/Main ][Conn:Est] 建立来自({client_address[0]}:{client_address[1]})的({client_type})连接")

        try:
            if client_type == 'OneBot/11':
                if self.OneBotClients != None:
                    logger.info(
                        f"[ Msg/Main ][AuthDeny] (X) 已经存在({client_type})的未释放连接, 来自({client_address[0]}:{client_address[1]})的新连接被放弃")
                else:
                    self.OneBotClients = websocket
                await self._handler_OneBotClient()

            elif client_type == 'MaaCtrl/00':
                if self.MaaCtrlClients != None:
                    logger.info(
                        f"[ Msg/Main ][AuthDeny] (X) 已经存在({client_type})的未释放连接, 来自({client_address[0]}:{client_address[1]})的新连接被放弃")
                else:
                    self.MaaCtrlClients = websocket
                await self._handler_MaaCtrlClient()

        except ConnectionClosedError as e:
            logger.error(f"[ Msg/Main ][Conn:Out] (X) 连接异常断开: {client_address}。错误码/状态：{e.code}。")
        except ConnectionClosedOK as e:
            logger.info(f"[ Msg/Main ][Conn:Out] 连接正常关闭: {client_address}。")
        except Exception as e:
            logger.error(f"[ Msg/Main ][?Unknown] 处理连接时发生致命错误。{e}")

        finally:
            await websocket.close()
            logger.info(f"[ Msg/Main ][Conn:Out] 连接关闭: {client_address}")
            if client_type == 'OneBot/11':
                self.OneBotClients = None
                logger.info(f"[  Msg/One ][ResetVar] 已经释放 OneBot/11")
            elif client_type == 'MaaCtrl/00':
                self.MaaCtrlClients = None
                logger.info(f"[  Msg/Maa ][ResetVar] 已经释放 MaaCtrl/00")
            else:
                logger.info(f"[  Msg/Any ][Conn:Out] Unknown ws client.")
                pass


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

    async def http_handler(request: WebRequest):
        client_info = request.remote or "Unknown"

        logger.info(f"收到来自 ({client_info}) 的 ({request.method}) 请求 ({api_path})")
        if request.method == 'POST' and request.content_type == 'application/json':
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


async def process_request(connection: ServerConnection, request: WS11Request):
    """
    为了解决以下来自 asyncio.wait 的报错 尝试使用此函数, 但暂未成功

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
    File "/miniconda3/Lib/site-packages/websockets/asyncio/server.py", line 207, in handshake
        raise self.protocol.handshake_exc
    websockets.exceptions.InvalidMessage: did not receive a valid HTTP request
    ```
    """
    # 拦截所有非 WebSocket 升级请求
    connection_hdr = request.headers.get("Connection", "").lower()
    peername = connection.transport.get_extra_info("peername")

    if "upgrade" not in connection_hdr:
        logger.warning(f"[ WS ] 来自 ({peername or None}) 的 HTTP 握手错误, 返回 400 BAD_REQUEST")
        return connection.respond(HTTPStatus.BAD_REQUEST, text="upgrade not in connection")

    # logger.warning(f"[ WS ] ({connection.subprotocol})")
    # -> [ WS ] (None)
    return None  # None = 继续 WebSocket 握手
