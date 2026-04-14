"""
WebSocket服务器模块 - Phase2重构
基于接口定义从web_server.py拆分出的独立模块
"""

import asyncio
import json
import logging
import time
import uuid6
import io
import base64
from typing import Dict, List, Optional, Any, AsyncGenerator
from PIL import Image, ImageDraw, ImageFont

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

from file_handler import write_config, load_cache
from src.logging_system import get_web_server_logger, log_exception

# Dify集成模块
from dify_client import DifyIntegration

# 消息处理模块
from message_processor import MessageProcessor

# 输出格式化模块
from output_formatter import (
    setup_rich_logging,
    LogTag,
)

# 状态报告图片生成模块 - 使用新的 layout_engine
from layout_engine import create_layout_engine

# 类型定义导入
try:
    from src.type_definitions import (
        ConfigDict,
        Message,
        WebSocketMessage,
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
    
    class Message(TypedDict):
        message_type: str
        user_id: int
        group_id: Optional[int]
        message: List[Dict[str, Any]]
    
    class WebSocketMessage(TypedDict):
        type: str
        data: Dict[str, Any]

# 使用 Rich 设置日志
logger = setup_rich_logging(logging.INFO)


def get_timestamp() -> str:
    """获取当前时间戳 HH:MM:SS"""
    return time.strftime("%H:%M:%S")


class ConnectionInfo:
    """WebSocket连接信息"""
    def __init__(self, client_id: str, connected_at: str, 
                 last_activity: str, remote_address: str):
        self.client_id = client_id
        self.connected_at = connected_at
        self.last_activity = last_activity
        self.remote_address = remote_address


class WebSocketServer:
    """WebSocket服务器核心接口"""
    
    def __init__(self, config: ConfigDict):
        """
        WebSocket服务器初始化
        
        Args:
            config: 配置字典，包含Dify API密钥、WebSocket设置等
        """
        self.config = config

        # 对话白名单
        react_info: list[dict[str, str | int]] = config.get("reaction_to_sender", "")
        self.active_group = [_.get("group_id", "") for _ in react_info if _.get("message_type", "") == "group"]
        self.active_private = [_.get("user_id", "") for _ in react_info if _.get("message_type", "") == "private"]
        logger.debug(f"{LogTag.WS_SERVER} 白名单群组: {self.active_group} | 白名单私聊: {self.active_private}")

        # websocket 客户端列表
        self.client_info: list[dict[str, str]] = config.get("client_info", "")
        # 构建 UA->token 映射，便于快速查找
        self.ua_token_map = {info.get("UA", ""): info.get("token", "") for info in self.client_info}
        logger.debug(f"{LogTag.WS_SERVER} UA映射: {list(self.ua_token_map.keys())}")

        # 本地状态缓存
        self.maa_reports_cache: dict[str, Any] = load_cache("cache.json")
        self.dashboard_runtime = None
        self.latest_report_image: Image.Image | None = None
        self.latest_screenshot_image: Image.Image | None = None

        # 用户映射
        self.user_map: dict[str, dict[str, Any]] = config.get("user_map", {})
        self.reverse_user_map: dict[int, str] = {
            user_data["user_id"]: config_name
            for config_name, user_data in self.user_map.items()
        }
        logger.debug(f"{LogTag.WS_SERVER} 反向用户映射: {self.reverse_user_map}")

        self.msg_route: dict[str, Any] = config.get("msg_route", {})

        # 会话 WS 集
        self.OneBotClients: Optional[ServerConnection] = None
        self.MaaCtrlClients: Optional[ServerConnection] = None

        # 消息等待池
        self.waiting_pool: dict[str, asyncio.Future[Any]] = {}

        # dify配置
        self.dify_api_key: str = config.get("dify_api_key", "")
        self.dify_base_url: str = config.get("dify_base_url", "")

        # 通知模板配置
        self.notifications: dict[str, Any] = config.get("notifications", {})

        logger.info(f"{LogTag.WS_SERVER} 配置加载完成 | 用户数: {len(self.user_map)} | 活跃群组: {len(self.active_group)}")

    async def start(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        """启动WebSocket服务器（接口兼容方法）"""
        # 实际启动逻辑在notify_host.py中，这里保持接口兼容
        pass

    async def stop(self) -> None:
        """停止WebSocket服务器（接口兼容方法）"""
        # 清理资源
        if self.OneBotClients:
            await self.OneBotClients.close()
            self.OneBotClients = None
        if self.MaaCtrlClients:
            await self.MaaCtrlClients.close()
            self.MaaCtrlClients = None
        logger.info(f"{LogTag.WS_SERVER} 服务器已停止")

    def attach_dashboard_runtime(self, runtime: Any) -> None:
        self.dashboard_runtime = runtime

    def notify_dashboard_state_changed(self) -> None:
        if self.dashboard_runtime is not None:
            self.dashboard_runtime.on_state_changed()

    def get_dashboard_cache(self) -> dict[str, Any]:
        return self.maa_reports_cache.copy()

    def get_latest_report_image(self) -> Image.Image | None:
        return self.latest_report_image

    def get_latest_screenshot_image(self) -> Image.Image | None:
        return self.latest_screenshot_image

    @staticmethod
    def _normalize_execution_configs(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _persist_maa_cache(self) -> None:
        write_config(data=self.maa_reports_cache, config_path="cache.json")
        self.notify_dashboard_state_changed()

    def _apply_maa_runtime_state(self, update_status: str) -> None:
        if update_status == "Starting":
            self.maa_reports_cache["state"] = "Running"
            self.maa_reports_cache["progressPhase"] = "not_started"
            self.maa_reports_cache["lastCompletedAt"] = 0.0
        elif update_status == "Next_Step":
            self.maa_reports_cache["state"] = "Running"
            self.maa_reports_cache["progressPhase"] = "running"
            self.maa_reports_cache["lastCompletedAt"] = 0.0
        elif update_status == "Reconnect":
            self.maa_reports_cache["state"] = "Running"
            self.maa_reports_cache["progressPhase"] = (
                "running" if str(self.maa_reports_cache.get("Step") or "").strip() else "not_started"
            )
        elif update_status == "AllCompleted":
            self.maa_reports_cache["state"] = "Idle"
            self.maa_reports_cache["progressPhase"] = "completed"
            self.maa_reports_cache["lastCompletedAt"] = time.time()
        elif update_status == "Failed":
            self.maa_reports_cache["state"] = "Idle"
            self.maa_reports_cache["progressPhase"] = "failed"
            self.maa_reports_cache["lastCompletedAt"] = 0.0
        elif update_status == "ManuallyStopped":
            self.maa_reports_cache["state"] = "Idle"
            self.maa_reports_cache["progressPhase"] = "stopped"
            self.maa_reports_cache["lastCompletedAt"] = 0.0

    def _mark_maactrl_disconnected(self) -> None:
        self.maa_reports_cache["Connection"] = "Disconnected"
        self.maa_reports_cache["lastUpdate"] = time.time()
        self._persist_maa_cache()

    def get_message_text(self, message_dict: Message, lower: bool = True) -> str:
        """
        从消息字典中提取文本内容
        
        Args:
            message_dict: 消息字典
            lower: 是否转换为小写
        
        Returns:
            str: 提取的文本内容
        """
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
        original_msg: Message,
        reply_text: list[str] = None,
        at: Optional[int] = None,
        attach_image: list[tuple[Any, str]] = None
    ) -> WebSocketMessage:
        """
        构造WebSocket消息

        Args:
            original_msg: 原始消息
            reply_text: 回复文本列表
            at: @的用户ID（仅群聊有效）
            attach_image: 附加图片列表，格式为[(Image对象或URL, 描述), ...]
                支持: PIL.Image.Image / str (http开头或base64://)

        Returns:
            WebSocketMessage: 构造的WebSocket消息
        """
        if reply_text is None:
            reply_text = []
        if attach_image is None:
            attach_image = []
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
            logger.error(f"{LogTag.MSG_MAIN} original message type is neither group or private")
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

        # 处理图片附件
        for img, img_summary in attach_image:
            if isinstance(img, Image.Image):
                # PIL Image - convert to base64
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                img_base64 = base64.b64encode(buffered.getvalue()).decode()
                structured_message.append({
                    "type": "image",
                    "data": {"file": f"base64://{img_base64}", "summary": img_summary}
                })
            elif isinstance(img, str) and (img.startswith("http") or img.startswith("base64://")):
                structured_message.append({
                    "type": "image",
                    "data": {"file": img, "summary": img_summary}
                })
            else:
                logger.error(f"{LogTag.MSG_MAIN} Ignoring img with corrupted format")

        inner_data.update({"message": structured_message})

        # 合并消息
        websocket_data.update({"params": inner_data})
        # 添加标识符
        websocket_data.update({"echo": str(uuid6.uuid7())})
        return websocket_data

    def check_connection(self, websocket: ServerConnection) -> tuple[bool, Optional[str]]:
        """
        检查WebSocket连接的有效性
        
        Args:
            websocket: WebSocket连接对象
        
        Returns:
            tuple[bool, Optional[str]]: (是否有效, 客户端UA标识)
        """
        client_address = websocket.remote_address
        # 检查连接信息
        if not getattr(websocket, "request", None):
            logger.warning(f"{LogTag.CHECK_CONN} 拒绝连接 | 地址: {client_address[0]}:{client_address[1]} | 原因: request为空")
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
            logger.warning(f"{LogTag.CHECK_CONN} 拒绝连接 | 地址: {client_address[0]}:{client_address[1]} | 原因: UA不匹配 ({client_user_agent})")
            return False, None

        # Token校验
        expected_token = self.ua_token_map[client_user_agent]
        if client_auth != expected_token:
            logger.error(f"{LogTag.CHECK_CONN} 认证失败 | UA: {client_user_agent} | 地址: {client_address[0]}:{client_address[1]}")
            return False, None

        return True, client_user_agent

    def _capture_game_window(self) -> Optional[Image.Image]:
        """
        捕获游戏窗口
        使用单独的 window_capture 模块
        """
        from window_capture import capture_game_window
        return capture_game_window()

    def generate_status_report_image(self) -> Image.Image:
        """
        生成MAA状态报告图片
        使用新的 layout_engine 模块
        从缓存中实时获取当前进度和用户信息

        Returns:
            Image.Image: PIL Image对象
        """
        # 延迟初始化 layout_engine（避免循环导入）
        if not hasattr(self, '_layout_engine'):
            config_manager, renderer = create_layout_engine("layout_config.json")
            self._layout_config_manager = config_manager
            self._layout_renderer = renderer

        # 从缓存获取实时数据（匹配 async_main.py 的字段名）
        cache = {
            "CurruentUser": self.maa_reports_cache.get("CurruentUser") or "Unknown",
            "NextUser": self.maa_reports_cache.get("NextUser") or "",
            "Step": self.maa_reports_cache.get("Step") or "1",
            "TotalSteps": self.maa_reports_cache.get("TotalSteps") or "1",
            "Status": self.maa_reports_cache.get("Status") or "Idle"
        }

        # 捕获游戏窗口
        game_img = self._capture_game_window()
        if game_img:
            logger.debug(f"成功捕获游戏窗口: {game_img.size}")
        else:
            logger.debug("未找到游戏窗口，使用离线状态")

        # 使用 layout_engine 渲染
        try:
            img = self._layout_renderer.render(cache, game_img=game_img)
            return img.convert("RGB")
        except Exception as e:
            logger.error(f"生成状态报告图片失败: {e}")
            # 返回错误图片
            error_img = Image.new('RGB', (800, 400), 'white')
            error_draw = ImageDraw.Draw(error_img)
            try:
                font = ImageFont.truetype("msyhbd.ttc", 32)
            except:
                font = ImageFont.load_default()
            error_draw.text((50, 100), "MAA 状态报告生成失败", fill='#CC0000', font=font)
            error_draw.text((50, 160), f"错误: {str(e)[:50]}", fill='#666666', font=font)
            return error_img

    async def _handler_OneBotClient(self) -> None:
        """
        处理OneBot客户端的WebSocket消息
        """
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
                        _cmd = chat_message_text.removeprefix("maa").strip() or "help"
                        _src = "群" if message_type == "group" else "私"
                        logger.info(f"{LogTag.MSG_ONEBOT} 收到命令 | 来源: {_src} | 用户: {user_id} | 命令: {_cmd}")
                        logger.debug(f"{LogTag.MSG_ONEBOT} 完整内容:\n{message_dict}\n")
                        chat_command = chat_message_text.removeprefix("maa").strip()

                        if chat_command in ["help", ""]:
                            help_text = format_help_message_tabulate()
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict, reply_text=[help_text]
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

                                    status_text = format_connection_status_tabulate(
                                        latency_onebot=latency_1,
                                        latency_maa=latency_2,
                                        maa_connected=True
                                    )
                                    reply_data = await self.make_websocket_msg(
                                        original_msg=message_dict,
                                        reply_text=[status_text]
                                    )
                                except ConnectionClosed as e:
                                    logger.warning(f"{LogTag.MSG_ONEBOT} MAA控制器连接已断开: {e}")
                                    status_text = format_connection_status_tabulate(
                                        latency_onebot=latency_1,
                                        maa_connected=False
                                    )
                                    reply_data = await self.make_websocket_msg(
                                        original_msg=message_dict,
                                        reply_text=[status_text]
                                    )
                                    self.maa_reports_cache.update({"Connection": "Unreachable"})
                                    write_config(data=self.maa_reports_cache, config_path="cache.json")
                                    self.notify_dashboard_state_changed()
                            else:
                                status_text = format_connection_status_tabulate(
                                    latency_onebot=latency_1,
                                    maa_connected=False
                                )
                                reply_data = await self.make_websocket_msg(
                                    original_msg=message_dict,
                                    reply_text=[status_text]
                                )
                                self.maa_reports_cache.update({"Connection": "Unreachable"})
                                write_config(data=self.maa_reports_cache, config_path="cache.json")
                                self.notify_dashboard_state_changed()

                        elif chat_command in ["现在", "当前", "currentuser", "current"]:
                            current_user = self.maa_reports_cache.get("CurruentUser") or "未知"
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[f"MAA 当前正在执行: {current_user}"]
                            )

                        elif chat_command in ["下一个", "即将", "nextuser", "next"]:
                            next_user = self.maa_reports_cache.get("NextUser") or "未知"
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[f"MAA 下一个将执行: {next_user}"]
                            )

                        elif chat_command in ["host", "控制器", "controller", "status"]:
                            state = self.maa_reports_cache.get("state", "Idle")
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=[f"MAA 控制器状态: {state}"]
                            )

                        elif chat_command in ["report"]:
                            # 生成状态报告图片
                            report_image = self.generate_status_report_image()
                            self.latest_report_image = report_image
                            self.notify_dashboard_state_changed()
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=["MAA Status Report:"],
                                attach_image=[(report_image, "MAA Status Report")]
                            )
                        else:
                            logger.info(f"{LogTag.MSG_ONEBOT} 未识别MAA命令，改为引导访问Dashboard | 命令: {chat_command}")
                            reply_data = await self.make_websocket_msg(
                                original_msg=message_dict,
                                reply_text=["访问 https://maa.nslc.top 以查看详情"]
                            )

                        # 发送消息
                        msg_id = reply_data.get("echo", "")

                        logger.info(f'{LogTag.MSG_ONEBOT} 准备发送消息 | ID: {msg_id[:8]}...')
                        logger.debug(f"{LogTag.MSG_ONEBOT} 完整内容:\n{reply_data}\n")
                        await self.OneBotClients.send(json.dumps(reply_data))

                        # 等待服务器确认
                        while True:
                            response = await self.OneBotClients.recv()
                            response_data: dict[str, Any] = json.loads(response)
                            if (echo := response_data.get("echo", "")) and echo == msg_id:
                                _status = response_data.get("status", "")
                                if _status == 'ok':
                                    logger.info(f"{LogTag.MSG_ONEBOT} 消息发送成功 | ID: {msg_id[:8]}...")
                                else:
                                    logger.warning(f"{LogTag.MSG_ONEBOT} 消息发送失败 | ID: {msg_id[:8]}... | 状态: {_status}")
                                logger.debug(f"{LogTag.MSG_ONEBOT} 响应内容:\n{response_data}\n")
                                break
                            else:
                                logger.debug(f"{LogTag.MSG_ONEBOT} 丢弃消息 (无echo): {response[:100]}")
                                continue

            except Exception as e:
                log_exception(logger, e, f"{LogTag.MSG_ONEBOT} 未知错误：处理消息时发生意外错误")

    async def _handler_MaaCtrlClient(self) -> None:
        """
        处理MAA控制客户端的WebSocket消息
        """
        client_type = "MaaCtrlClients"
        if not (websocket := self.MaaCtrlClients):
            raise ValueError(f"{client_type} is {self.MaaCtrlClients}")
        # start_time 会在 starting 部分 覆盖，此处保证变量绑定
        start_time = time.time()

        async for ws_income_message in websocket:
            try:
                # 解析并补充JSON
                message_dict: dict[str, Any] = json.loads(ws_income_message)
                if "ExecutionConfigs" in message_dict:
                    message_dict["ExecutionConfigs"] = self._normalize_execution_configs(
                        message_dict.get("ExecutionConfigs")
                    )
                
                # 提取关键信息用于日志摘要
                # 匹配 async_main.py 的字段名
                _user: str = message_dict.get("CurruentUser") or "未知"
                _status: str = message_dict.get("Status") or "未知"
                _step: str = message_dict.get("Step") or "?"
                _total: str = message_dict.get("TotalSteps") or "?"

                logger.info(f"{LogTag.MSG_MAA} 收到MAA状态 | 用户: {_user} | 状态: {_status} | 步骤: {_step}/{_total}")
                logger.debug(f"{LogTag.MSG_MAA} 完整内容:\n{message_dict}\n")
                message_dict.update({"lastUpdate": time.time()})

                # 上一个Update的信息缓存
                last_user: str = self.maa_reports_cache.get("CurruentUser") or ""
                last_update: float = self.maa_reports_cache.get("lastUpdate", 0.0)
                duration = (time.time() - last_update) / 60 if last_update > 0 else 0

                # 更新本地缓存
                self.maa_reports_cache.update(message_dict)
                update_status = message_dict.get("Status", "")
                self._apply_maa_runtime_state(update_status)
                self._persist_maa_cache()
                notify_message_list: list[str] = []

                user: str = message_dict.get("CurruentUser") or ""

                # 个性化通知
                if update_status == "Next_Step":
                    logger.debug(f"{LogTag.MSG_MAA} Preparing At message")
                    # 查本地表
                    if not user or user not in self.user_map:
                        logger.warning(f"{LogTag.MSG_MAA} 未知用户: {user}")
                        continue
                    user_data = self.user_map[user]
                    user_id: int = user_data.get("user_id", "")
                    group_id: int = user_data.get("group_id", "")
                    message_type: str = user_data.get("message_type", "")

                    # 通用通知, @会被置于句首,应当将提醒放在句首
                    message_dict.update(self.user_map[user])
                    
                    # 特别地，群聊发送时，获取上一个人信息
                    last_user_card = None
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
                        logger.info(f"{LogTag.MSG_MAA} 查询群成员信息 | 用户: {last_user} | 群组: {group_id}")
                        await self.OneBotClients.send(json.dumps(request_data))
                        future: asyncio.Future[Any] = asyncio.Future()
                        self.waiting_pool[msg_id] = future
                        _api_response: dict[str, Any] = await future

                        if _api_response:
                            logger.info(f"{LogTag.MSG_MAA} 获取群成员成功 | 用户: {last_user}")
                            logger.debug(f"{LogTag.MSG_MAA} 完整内容:\n{_api_response}\n")
                            _api_data: dict[str, Any] | None = _api_response.get("data", {})
                            if _api_data:
                                last_user_card = _api_data.get("card", "")
                            else:
                                # -> json{data:null} -> pyDict{data:None} 群里没这人
                                last_user_card = None
                    
                    # 从配置读取通知模板并替换占位符
                    notify_cfg = self.notifications.get("next_step", {})
                    if message_type == "group":
                        if last_user_card:
                            template = notify_cfg.get("group", {}).get("with_last_user",
                                "@{user_id} 即将开始运行MAA一键长草（20s），请注意\n\n{last_user}已完成,耗时{duration:.2f}分钟")
                            notification_text = template.format(
                                user_id=user_id,
                                last_user=last_user_card,
                                duration=duration
                            )
                        else:
                            template = notify_cfg.get("group", {}).get("without_last_user",
                                "@{user_id} 即将开始运行MAA一键长草（20s），请注意")
                            notification_text = template.format(user_id=user_id)
                    else:
                        template = notify_cfg.get("private", {}).get("message",
                            "即将开始运行MAA一键长草（20s），请注意")
                        notification_text = template
                    notify_message_list = [notification_text]

                    # 群内 @user_id, 私聊因为 message_dict 中的 message_type 为 private. 不会将@加入
                    reply_data = await self.make_websocket_msg(
                        original_msg=message_dict,
                        reply_text=notify_message_list,
                        at=user_id
                    )

                else:
                    # 非Next_Step状态只更新状态，不发送通知
                    continue

                if self.OneBotClients:
                    msg_id = reply_data.get("echo", "")
                    logger.info(f'{LogTag.MSG_MAA} 准备发送通知 | ID: {msg_id[:8]}... | 状态: {update_status}')
                    logger.debug(f'{LogTag.MSG_MAA} 完整内容:\n{reply_data}\n')
                    await self.OneBotClients.send(json.dumps(obj=reply_data))
                    future: asyncio.Future[Any] = asyncio.Future()
                    self.waiting_pool[msg_id] = future
                    try:
                        # cannot call recv while another coroutine is already running recv or recv_streaming
                        # response = await self.OneBotClients.recv()
                        response_data = await future
                        logger.info(f"{LogTag.MSG_MAA} 通知发送成功 | ID: {msg_id[:8]}...")
                        logger.debug(f"{LogTag.MSG_MAA} 响应内容:\n{response_data}\n")
                    except Exception as e:
                        # 如果发生异常 (如连接中断, 超时等), 可以取消future
                        if msg_id in self.waiting_pool:
                            future.cancel()
                        log_exception(logger, e, f"{LogTag.MSG_MAA} 通知发送失败")
                        raise e
                    finally:
                        # 确保清理等待池
                        if msg_id in self.waiting_pool:
                            del self.waiting_pool[msg_id]
                else:
                    logger.debug(f"{LogTag.MSG_MAA} OneBot未连接，跳过发送")

            except Exception as e:
                log_exception(logger, e, f"{LogTag.MSG_MAA} 未知错误：处理消息时发生意外错误")

    async def _handler_news(self) -> None:
        """
        临时：处理 news 前缀命令，调用 dify_chat_stream，并将返回值回发 OneBot。
        约定：调用前请将触发该命令的 message_dict 写入 self.msg_route（保持与现有风格一致，便于后续统一重构）。
        """
        client_type = "OneBotClients"
        if not (websocket := self.OneBotClients):
            raise ValueError(f"{client_type} is {self.OneBotClients}")

        message_dict: dict[str, Any] = self.msg_route
        if not message_dict:
            raise ValueError(f"[  Msg/One ][Dat:ERR] msg_route is {message_dict}")

        try:
            logger.info(f"{LogTag.MSG_ONEBOT} news command received, forwarding to dify")
            dify_answer: str = await asyncio.to_thread(
                dify_chat_stream,
                api_key=self.dify_api_key,
                base_url=self.dify_base_url,
                user_input="test",
            )
        except Exception as e:
            log_exception(logger, e, f"{LogTag.MSG_ONEBOT} dify request failed")
            dify_answer = f"Dify 调用失败：{e}"

        reply_data = await self.make_websocket_msg(
            original_msg=message_dict,
            reply_text=[dify_answer if dify_answer else "(empty dify response)"],
        )

        # 发送消息（保持与当前 OneBot 分支一致：send 后 recv 等 echo 确认）
        msg_id = reply_data.get("echo", "")

        logger.info(f'{LogTag.MSG_ONEBOT} Ready to send message {msg_id}')
        logger.debug(f"{LogTag.MSG_ONEBOT} Detail:\n{reply_data}\n")
        await websocket.send(json.dumps(reply_data))

        # 等待服务器确认
        while True:
            response = await websocket.recv()
            response_data: dict[str, Any] = json.loads(response)
            if (echo := response_data.get("echo", "")) and echo == msg_id:
                if response_data.get("status", "") == 'ok':
                    logger.info(f"{LogTag.MSG_ONEBOT} Message send confirmed {msg_id}")
                    logger.debug(f"{LogTag.MSG_ONEBOT} Detail:\n{response_data}\n")
                else:
                    logger.warning(f"{LogTag.MSG_ONEBOT} Message send FAILED {msg_id}")
                    logger.debug(f"{LogTag.MSG_ONEBOT} Detail:\n{response_data}\n")
                break
            else:
                logger.debug(f"{LogTag.MSG_ONEBOT} Discarding msg without echo: {response}")
                continue

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

        logger.info(f"{LogTag.MSG_MAIN} 客户端已连接 | 类型: {client_type} | 地址: {client_address[0]}:{client_address[1]}")

        try:
            if client_type == 'OneBot/11':
                if self.OneBotClients is not None:
                    logger.info(
                        f"{LogTag.MSG_MAIN} 已经存在({client_type})的未释放连接, 来自({client_address[0]}:{client_address[1]})的新连接被放弃")
                else:
                    self.OneBotClients = websocket
                await self._handler_OneBotClient()

            elif client_type == 'MaaCtrl/00':
                if self.MaaCtrlClients is not None:
                    logger.info(
                        f"{LogTag.MSG_MAIN} 已经存在({client_type})的未释放连接, 来自({client_address[0]}:{client_address[1]})的新连接被放弃")
                else:
                    self.MaaCtrlClients = websocket
                await self._handler_MaaCtrlClient()

        except ConnectionClosedError as e:
            logger.error(f"{LogTag.MSG_MAIN} 连接异常断开 | 地址: {client_address[0]}:{client_address[1]} | 代码: {e.code} | 原因: {e.reason}")
        except ConnectionClosedOK as e:
            logger.info(f"{LogTag.MSG_MAIN} 连接正常关闭 | 地址: {client_address[0]}:{client_address[1]} | 代码: {e.code} | 原因: {e.reason}")
        except Exception as e:
            log_exception(logger, e, f"{LogTag.MSG_MAIN} 处理连接时发生致命错误")

        finally:
            await websocket.close()
            logger.info(f"{LogTag.MSG_MAIN} 客户端连接已关闭 | 地址: {client_address[0]}:{client_address[1]}")
            if client_type == 'OneBot/11':
                self.OneBotClients = None
                logger.info(f"{LogTag.MSG_ONEBOT} OneBot客户端已断开 | 类型: OneBot/11")
            elif client_type == 'MaaCtrl/00':
                self.MaaCtrlClients = None
                self._mark_maactrl_disconnected()
                logger.info(f"{LogTag.MSG_MAA} MaaCtrl客户端已断开 | 类型: MaaCtrl/00")
            else:
                logger.debug(f"{LogTag.MSG_MAIN} 未知客户端类型: {client_type}")

    async def broadcast(self, message: Dict[str, Any]) -> int:
        """
        广播消息到所有连接客户端
        
        Args:
            message: 要广播的消息字典
        
        Returns:
            int: 成功发送的客户端数量
        """
        sent_count = 0
        message_json = json.dumps(message)
        
        # 发送给OneBot客户端
        if self.OneBotClients:
            try:
                await self.OneBotClients.send(message_json)
                sent_count += 1
            except Exception as e:
                logger.error(f"{LogTag.BROADCAST} 广播到OneBot失败 | 错误: {str(e)[:50]}")

        # 发送给MAA控制客户端
        if self.MaaCtrlClients:
            try:
                await self.MaaCtrlClients.send(message_json)
                sent_count += 1
            except Exception as e:
                logger.error(f"{LogTag.BROADCAST} 广播到MaaCtrl失败 | 错误: {str(e)[:50]}")

        logger.info(f"{LogTag.BROADCAST} 广播完成 | 成功发送: {sent_count} 个客户端")
        return sent_count

