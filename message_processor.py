"""
消息处理模块 - MAA OneBot Adapter v2.1

基于 web_server.py 剩余业务逻辑重构，负责：
1. 消息路由和分发
2. 业务逻辑处理
3. 响应生成和格式化
4. 与各服务模块协调
"""

import json
import logging
from typing import Dict, List, Any, Optional, Callable, AsyncGenerator
import asyncio

from src.logging_config import get_logger
from dify_client import DifyIntegration
from src.config_manager import ConfigManager
from llm_parser import parse_command

logger = get_logger(__name__)


class MessageProcessor:
    """消息处理服务接口"""
    
    def __init__(
        self, 
        dify_service: DifyIntegration, 
        config_manager: ConfigManager
    ):
        """初始化消息处理器"""
        self.dify_service = dify_service
        self.config_manager = config_manager
        self.handlers: Dict[str, Callable] = {}
        self.maa_reports_cache: Dict[str, Any] = {}
        
        # 注册默认处理器
        self._register_default_handlers()
    
    def _register_default_handlers(self) -> None:
        """注册默认消息处理器"""
        self.add_handler("chat_message", self.handle_chat_message)
        self.add_handler("system_message", self.handle_system_message)
        self.add_handler("news_request", self.handle_news_request)
        self.add_handler("maa_report", self.handle_maa_report)
    
    async def process_message(self, message: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """
        处理消息（核心业务逻辑）
        
        Args:
            message: 原始消息字典
            client_id: 客户端标识符
            
        Returns:
            处理后的响应消息
        """
        try:
            # 验证消息格式
            if not self.validate_message(message):
                return self._create_error_response("消息格式无效", message)
            
            # 规范化消息
            normalized_msg = self.normalize_message(message)
            
            # 根据消息类型路由到相应处理器
            msg_type = normalized_msg.get("type", "unknown")
            handler = self.handlers.get(msg_type)
            
            if handler:
                return await handler(normalized_msg, client_id)
            else:
                # 默认处理：聊天消息
                return await self.handle_chat_message(normalized_msg, client_id)
                
        except Exception as e:
            logger.error(f"处理消息时发生错误: {e}", exc_info=True)
            return self._create_error_response(f"处理失败: {str(e)}", message)
    
    async def handle_chat_message(self, message: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理聊天消息（保持现有逻辑）"""
        chat_text = message.get("content", "").strip()
        
        # 处理MAA命令
        if chat_text.lower().startswith("maa"):
            command = chat_text[3:].strip()
            return await self._handle_maa_command(command, message, client_id)
        
        # 处理新闻请求
        elif chat_text.lower() == "news":
            return await self.handle_news_request(message, client_id)
        
        # 其他聊天消息转发到Dify
        else:
            return await self._handle_general_chat(chat_text, message, client_id)
    
    async def handle_news_request(self, message: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理新闻请求"""
        logger.info(f"收到新闻请求来自客户端 {client_id}")
        
        if not self.dify_service:
            error_msg = "Dify功能未配置或不可用"
            logger.warning("Dify集成不可用")
            return self._create_response(error_msg, message)
        
        try:
            logger.info("新闻命令收到，转发到Dify")
            
            # 使用DifyIntegration的chat_stream方法
            dify_response = await self.dify_service.chat_stream(
                user_input="news",
                user=client_id
            )
            
            # 格式化Dify响应
            if isinstance(dify_response, str):
                answer = dify_response
            elif isinstance(dify_response, dict):
                answer = dify_response.get("answer", "Dify返回了空响应")
            else:
                answer = str(dify_response)
            
            return self._create_response(answer, message)
            
        except Exception as e:
            logger.error(f"Dify调用失败: {e}", exc_info=True)
            return self._create_response(f"Dify调用失败：{str(e)}", message)
    
    async def handle_system_message(self, message: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理系统消息"""
        msg_type = message.get("subtype", "unknown")
        
        if msg_type == "status_request":
            return await self._handle_status_request(message, client_id)
        elif msg_type == "config_update":
            return await self._handle_config_update(message, client_id)
        else:
            return self._create_response(f"未知系统消息类型: {msg_type}", message)
    
    async def handle_maa_report(self, message: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理MAA报告"""
        report_data = message.get("data", {})
        
        # 更新缓存
        self.maa_reports_cache.update(report_data)
        
        # 记录日志
        logger.info(f"收到MAA报告: {report_data.get('type', 'unknown')}")
        
        # 返回确认响应
        return {
            "type": "maa_report_ack",
            "status": "success",
            "timestamp": asyncio.get_event_loop().time(),
            "data": {"received": True}
        }
    
    async def _handle_maa_command(self, command: str, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理MAA命令"""
        command = command.lower()
        
        # 命令映射
        command_handlers = {
            "help": self._handle_maa_help,
            "test": self._handle_maa_test,
            "ws": self._handle_maa_ws_status,
            "currentuser": self._handle_maa_current_user,
            "nextuser": self._handle_maa_next_user,
            "host": self._handle_maa_host_status,
            "report": self._handle_maa_report_command,
        }
        
        # 查找处理器
        for cmd_key, handler in command_handlers.items():
            if command in [cmd_key, cmd_key.replace("_", "")]:
                return await handler(original_msg, client_id)
        
        # 未知命令转发到LLM
        return await self._handle_unknown_command(command, original_msg, client_id)
    
    async def _handle_maa_help(self, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理MAA帮助命令"""
        help_text = [
            "提示词MAA, 大小写通用",
            "命令/别名 功能描述",
            "help （空）显示本帮助信息",
            "测试/test 测试回复状态，返回值为['测试收到']",
            "ws状态/ws 查询控制器的Web Socket连接状态",
            "现在/currentuser/... 查询当前正在执行的配置用户",
            "下一个/nextuser/... 查询MAA在当前配置执行完成后，即将执行的下一配置用户",
            "控制器/host/... 查询MAA和控制器当前的状态",
            "report 返回一个包含所有关键状态信息的详细报告",
        ]
        return self._create_response("\n".join(help_text), original_msg)
    
    async def _handle_maa_test(self, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理测试命令"""
        return self._create_response("测试收到", original_msg)
    
    async def _handle_maa_ws_status(self, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理WebSocket状态查询"""
        # 这里需要实际的WebSocket状态检查逻辑
        # 暂时返回模拟数据
        status = {
            "websocket": "connected",
            "maa_controller": "connected",
            "latency": "15ms"
        }
        return self._create_response(f"WebSocket状态: {status}", original_msg)
    
    async def _handle_maa_current_user(self, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理当前用户查询"""
        current_user = self.maa_reports_cache.get("CurruentUser", "未知")
        return self._create_response(f"MAA当前正在执行 {current_user} 的配置", original_msg)
    
    async def _handle_maa_next_user(self, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理下一个用户查询"""
        next_user = self.maa_reports_cache.get("NextUser", "未知")
        return self._create_response(f"MAA下一个将执行 {next_user} 的配置", original_msg)
    
    async def _handle_maa_host_status(self, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理主机状态查询"""
        status = self.maa_reports_cache.get("Status", "未知")
        return self._create_response(f"MAA和控制器当前的状态为 {status}", original_msg)
    
    async def _handle_maa_report_command(self, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理报告命令"""
        report_lines = [
            f"当前配置: {self.maa_reports_cache.get('CurruentUser', '未知')}",
            f"下一配置: {self.maa_reports_cache.get('NextUser', '未知')}",
            f"WebSocket连接: {self.maa_reports_cache.get('Connection', '未知')}",
            f"控制器状态: {self.maa_reports_cache.get('Status', '未知')}",
        ]
        return self._create_response("\n".join(report_lines), original_msg)
    
    async def _handle_general_chat(self, chat_text: str, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理一般聊天消息"""
        if not self.dify_service:
            return self._create_response("AI聊天功能暂不可用", original_msg)
        
        try:
            # 调用Dify服务
            dify_response = await self.dify_service.chat_stream(
                user_input=chat_text,
                user=client_id
            )
            
            # 格式化响应
            if isinstance(dify_response, str):
                answer = dify_response
            elif isinstance(dify_response, dict):
                answer = dify_response.get("answer", "AI返回了空响应")
            else:
                answer = str(dify_response)
            
            return self._create_response(answer, original_msg)
            
        except Exception as e:
            logger.error(f"AI聊天失败: {e}", exc_info=True)
            return self._create_response(f"AI聊天失败：{str(e)}", original_msg)
    
    async def _handle_unknown_command(self, command: str, original_msg: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理未知命令（转发到LLM）"""
        try:
            # 使用现有的parse_command函数
            gemini_key = self.config_manager.get("gemini_key", "")
            if not gemini_key:
                return self._create_response("Gemini API密钥未配置", original_msg)
            
            new_command = await parse_command(gemini_key, command)
            
            # 添加配置信息
            config_name = "default"  # 这里需要根据用户ID获取实际配置
            new_command.update({"config": config_name})
            
            response_text = f"测试阶段，仅返回LLM输出: {str(new_command).replace('\'', '')}"
            return self._create_response(response_text, original_msg)
            
        except Exception as e:
            logger.error(f"LLM解析失败: {e}", exc_info=True)
            return self._create_response(f"命令解析失败：{str(e)}", original_msg)
    
    async def _handle_status_request(self, message: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理状态请求"""
        status_info = {
            "service": "MAA OneBot Adapter",
            "version": "2.1",
            "status": "running",
            "modules": {
                "websocket": "active",
                "http": "active",
                "dify": "active" if self.dify_service else "inactive",
                "config": "active",
                "logging": "active",
                "message_processor": "active"
            },
            "timestamp": asyncio.get_event_loop().time()
        }
        return {
            "type": "status_response",
            "status": "success",
            "data": status_info
        }
    
    async def _handle_config_update(self, message: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """处理配置更新"""
        new_config = message.get("config", {})
        
        try:
            # 更新配置管理器
            for key, value in new_config.items():
                self.config_manager.set(key, value)
            
            # 保存配置
            if self.config_manager.save():
                return {
                    "type": "config_update_response",
                    "status": "success",
                    "message": "配置更新成功"
                }
            else:
                return {
                    "type": "config_update_response",
                    "status": "error",
                    "message": "配置保存失败"
                }
                
        except Exception as e:
            return {
                "type": "config_update_response",
                "status": "error",
                "message": f"配置更新失败: {str(e)}"
            }
    
    def add_handler(self, message_type: str, handler: Callable) -> None:
        """添加消息处理器"""
        self.handlers[message_type] = handler
        logger.debug(f"注册消息处理器: {message_type} -> {handler.__name__}")
    
    def remove_handler(self, message_type: str) -> None:
        """移除消息处理器"""
        if message_type in self.handlers:
            del self.handlers[message_type]
            logger.debug(f"移除消息处理器: {message_type}")
    
    def get_handlers(self) -> Dict[str, str]:
        """获取所有处理器信息"""
        return {msg_type: handler.__name__ for msg_type, handler in self.handlers.items()}
    
    def validate_message(self, message: Dict[str, Any]) -> bool:
        """验证消息格式"""
        required_fields = ["type", "timestamp"]
        
        for field in required_fields:
            if field not in message:
                logger.warning(f"消息缺少必要字段: {field}")
                return False
        
        # 验证时间戳格式
        timestamp = message.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            logger.warning(f"时间戳格式无效: {timestamp}")
            return False
        
        return True
    
    def normalize_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """规范化消息格式"""
        normalized = message.copy()
        
        # 确保有content字段
        if "content" not in normalized:
            normalized["content"] = ""
        
        # 确保有sender字段
        if "sender" not in normalized:
            normalized["sender"] = "unknown"
        
        return normalized
    
    def _create_response(self, text: str, original_msg: Dict[str, Any]) -> Dict[str, Any]:
        """创建标准响应消息"""
        return {
            "type": "chat_response",
            "echo": original_msg.get("echo", ""),
            "timestamp": asyncio.get_event_loop().time(),
            "status": "success",
            "data": {
                "content": text,
                "original_message_id": original_msg.get("message_id", "")
            }
        }
    
    def _create_error_response(self, error_msg: str, original_msg: Dict[str, Any]) -> Dict[str, Any]:
        """创建错误响应消息"""
        return {
            "type": "error_response",
            "echo": original_msg.get("echo", ""),
            "timestamp": asyncio.get_event_loop().time(),
            "status": "error",
            "error": error_msg,
            "data": {
                "original_message_id": original_msg.get("message_id", "")
            }
        }
    
    def update_maa_cache(self, cache_data: Dict[str, Any]) -> None:
        """更新MAA缓存数据"""
        self.maa_reports_cache.update(cache_data)
        logger.debug(f"MAA缓存已更新: {list(cache_data.keys())}")
    
    def get_maa_cache(self) -> Dict[str, Any]:
        """获取MAA缓存数据"""
        return self.maa_reports_cache.copy()


# 向后兼容的辅助函数
async def process_chat_message(message: Dict[str, Any], dify_service: DifyIntegration, 
                              config_manager: ConfigManager) -> Dict[str, Any]:
    """
    向后兼容的聊天消息处理函数
    
    保持与现有代码的兼容性
    """
    processor = MessageProcessor(dify_service, config_manager)
    return await processor.handle_chat_message(message, "compatibility_client")


async def process_news_request(message: Dict[str, Any], dify_service: DifyIntegration) -> Dict[str, Any]:
    """
    向后兼容的新闻请求处理函数
    """
    from src.config_manager import ConfigManager
    
    # 创建临时配置管理器
    temp_config = ConfigManager()
    temp_config.load()
    
    processor = MessageProcessor(dify_service, temp_config)
    return await processor.handle_news_request(message, "compatibility_client")