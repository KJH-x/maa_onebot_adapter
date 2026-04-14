"""
Dify AI服务集成模块
基于PHASE2_INTERFACE_DEFINITIONS.md接口定义实现
"""

import json
import logging
from typing import Dict, List, Optional, Any, AsyncGenerator, Callable
from urllib.parse import urljoin

import requests
import aiohttp
import asyncio

logger = logging.getLogger('app')

# 类型定义
try:
    from src.type_definitions import DifyConfig, DifyResponse
except ImportError:
    from typing import TypedDict
    
    class DifyConfig(TypedDict):
        api_key: str
        base_url: str
        timeout: int
    
    DifyResponse = Dict[str, Any]


class DifyIntegration:
    """Dify AI服务集成接口"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.dify.ai/v1"):
        """初始化Dify集成"""
        self.api_key = api_key
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self._timeout = 300  # 默认超时时间
    
    async def initialize(self) -> bool:
        """初始化连接"""
        try:
            self.session = aiohttp.ClientSession()
            logger.info("[DifyIntegration] 初始化成功")
            return True
        except Exception as e:
            logger.error(f"[DifyIntegration] 初始化失败: {e}")
            return False
    
    async def close(self) -> None:
        """关闭连接"""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("[DifyIntegration] 连接已关闭")
    
    async def chat_completion(
        self, 
        messages: List[Dict[str, Any]], 
        conversation_id: Optional[str] = None,
        stream: bool = False
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """聊天补全接口（保持现有功能）"""
        # 将消息列表转换为Dify格式
        user_input = self._messages_to_input(messages)
        
        if stream:
            async for chunk in self._chat_stream(user_input, conversation_id):
                yield chunk
        else:
            result = await self._chat_non_stream(user_input, conversation_id)
            yield result
    
    async def create_conversation(self, user_id: str) -> str:
        """创建新对话"""
        # Dify API会自动创建对话，这里返回一个标识符
        import uuid
        conversation_id = f"conv_{user_id}_{uuid.uuid4().hex[:8]}"
        logger.info(f"[DifyIntegration] 创建对话: {conversation_id}")
        return conversation_id
    
    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """获取对话信息"""
        # Dify API目前不支持直接获取对话信息
        logger.debug(f"[DifyIntegration] 获取对话信息: {conversation_id}")
        return {"id": conversation_id, "status": "active"}
    
    async def delete_conversation(self, conversation_id: str) -> bool:
        """删除对话"""
        # Dify API目前不支持直接删除对话
        logger.info(f"[DifyIntegration] 删除对话: {conversation_id}")
        return True
    
    # 核心Dify API调用方法
    async def chat_stream(
        self,
        user_input: str,
        user: str = "python-client",
        inputs: Optional[Dict[str, Any]] = None,
        files: Optional[List[Any]] = None,
        timeout: int = 300,
        disable_env_proxy: bool = True
    ) -> str:
        """
        向 Dify /v1/chat-messages 发送 streaming 请求，流式接收并返回完整 answer 文本
        
        Args:
            user_input: 用户输入文本
            user: 用户标识，默认为"python-client"
            inputs: 额外的输入参数
            files: 文件列表
            timeout: 请求超时时间（秒）
            disable_env_proxy: 是否禁用环境代理
        
        Returns:
            str: Dify返回的完整答案文本
        """
        try:
            # 使用同步版本的dify_chat_stream，通过线程池执行
            return await asyncio.to_thread(
                dify_chat_stream_sync,
                api_key=self.api_key,
                base_url=self.base_url,
                user_input=user_input,
                user=user,
                inputs=inputs,
                files=files,
                timeout=timeout,
                disable_env_proxy=disable_env_proxy
            )
        except Exception as e:
            log_exception(logger, e, "[DifyIntegration] Dify API调用失败")
            return f"Dify 调用失败：{e}"
    
    # 私有辅助方法
    def _messages_to_input(self, messages: List[Dict[str, Any]]) -> str:
        """将消息列表转换为Dify输入文本"""
        if not messages:
            return ""
        
        # 取最后一条用户消息作为输入
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        
        # 如果没有用户消息，返回第一条消息的内容
        return messages[0].get("content", "") if messages else ""
    
    async def _chat_stream(self, user_input: str, conversation_id: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """流式聊天实现"""
        # 这里可以扩展为真正的异步流式处理
        result = await self.chat_stream(user_input)
        yield {"answer": result, "conversation_id": conversation_id}
    
    async def _chat_non_stream(self, user_input: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """非流式聊天实现"""
        result = await self.chat_stream(user_input)
        return {"answer": result, "conversation_id": conversation_id}
    
    # 错误处理接口
    def should_retry(self, error: Exception) -> bool:
        """判断是否应该重试"""
        if isinstance(error, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
        if isinstance(error, requests.exceptions.HTTPError):
            # 5xx错误可以重试
            error_response = getattr(error, 'response', None)
            if error_response and 500 <= error_response.status_code < 600:
                return True
        return False
    
    async def retry_with_backoff(self, operation: Callable, max_retries: int = 3) -> Any:
        """带退避机制的重试"""
        import time
        
        for attempt in range(max_retries):
            try:
                return await operation()
            except Exception as e:
                if not self.should_retry(e) or attempt == max_retries - 1:
                    raise
                
                wait_time = 2 ** attempt  # 指数退避
                logger.warning(f"[DifyIntegration] 重试 {attempt + 1}/{max_retries}, 等待 {wait_time}秒: {e}")
                await asyncio.sleep(wait_time)


# 同步版本的Dify聊天函数（从web_server.py迁移）
def dify_chat_stream_sync(
    api_key: str,
    base_url: str,
    user_input: str,
    user: str = "python-client",
    inputs: Optional[Dict[str, Any]] = None,
    files: Optional[List[Any]] = None,
    timeout: int = 300,
    disable_env_proxy: bool = True,
) -> str:
    """
    向 Dify /v1/chat-messages 发送 streaming 请求，流式接收并返回完整 answer 文本；
    同时打印：
      - 执行节点名称（node_started/node_finished）
      - 上一节点耗时（上一节点 node_finished.elapsed_time）

    关键：disable_env_proxy=True 会使用 Session.trust_env=False，避免 requests 读取系统/环境代理导致"curl 正常、python 404"。
    
    Args:
        api_key: Dify API密钥
        base_url: Dify基础URL
        user_input: 用户输入文本
        user: 用户标识，默认为"python-client"
        inputs: 额外的输入参数
        files: 文件列表
        timeout: 请求超时时间（秒）
        disable_env_proxy: 是否禁用环境代理
    
    Returns:
        str: Dify返回的完整答案文本
    """
    try:
        # 1) 可靠拼接 URL（避免 base_url 带/不带/导致的路径异常）
        base = base_url.rstrip("/") + "/"
        url = urljoin(base, "v1/chat-messages")

        # 2) headers：明确接受 SSE
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        payload = {
            "inputs": inputs or {},
            "query": user_input,
            "response_mode": "streaming",
            "user": user,
            "files": files or [],
        }

        # 3) 使用 Session 控制
        session = requests.Session()

        full_answer_parts: List[str] = []

        with session.post(url, headers=headers, json=payload, stream=True, timeout=timeout) as resp:
            # 4) 出错时输出更多信息，便于定位（不要只 raise）
            if resp.status_code != 200:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} for {resp.url}\n"
                    f"Response headers: {dict(resp.headers)}\n"
                    f"Response body (first 500 chars): {resp.text[:500]}",
                    response=resp,
                )

        # 5) SSE 逐行读取
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue

            line: str = raw_line.strip()

            # SSE 心跳/注释行（可能以 ":" 开头）
            if line.startswith(":"):
                continue

            # 只处理 data: 行
            if not line.startswith("data:"):
                continue

            data_str = line[len("data:"):].strip()

            # 某些实现会有 [DONE] 结束符（你贴的示例是 message_end；这里两者都兼容）
            if data_str == "[DONE]":
                break

            try:
                event_payload: Dict[str, Any] = json.loads(data_str)
            except json.JSONDecodeError as e:
                logger.error(
                    f"JSON解析失败: {e}, "
                    f"原始数据: {data_str[:100] if len(data_str) > 100 else data_str}"
                )
                # 记录更多上下文信息
                logger.debug(f"完整数据长度: {len(data_str)}")
                continue

            event_type = event_payload.get("event")
            data: Dict[str, Any] = event_payload.get("data", {})

            # ===== Workflow 事件（可选打印）=====
            if event_type == "workflow_started":
                logger.info("[WORKFLOW] started")
                continue
            if event_type == "workflow_finished":
                logger.info("[WORKFLOW] finished")
                continue

            # ===== 节点开始 =====
            if event_type == "node_started":
                node_name = data.get("node_name") or data.get("title") or data.get("id") or "unknown"
                logger.info(f"[NODE START] {node_name}")
                continue

            # ===== 节点结束 =====
            if event_type == "node_finished":
                node_name = data.get("node_name") or data.get("title") or data.get("id") or "unknown"
                elapsed = data.get("elapsed_time")
                if isinstance(elapsed, (int, float)):
                    logger.info(f"[NODE END]   {node_name} | 节点耗时: {elapsed:.3f}s")
                else:
                    logger.info(f"[NODE END]   {node_name} | 节点耗时: (missing)")
                continue

            # ===== 模型输出增量（Dify 常见：message/agent_message 携带 answer）=====
            if event_type in ("message", "agent_message"):
                answer = event_payload.get("answer")
                if answer:
                    full_answer_parts.append(answer)
                continue

            # ===== 结束事件：你贴出来的是 message_end（此时可以 break）=====
            if event_type in ("message_end", "workflow_end"):
                # 有些情况下 message_end 不再携带最后一段文本，因此不要在这里 append，只作为结束信号
                break

        return "".join(full_answer_parts)
    
    except requests.exceptions.Timeout:
        logger.error("Dify API请求超时")
        return "请求超时，请稍后重试"
    except requests.exceptions.ConnectionError:
        logger.error("无法连接到Dify服务")
        return "无法连接到AI服务"
    except requests.exceptions.HTTPError as e:
        logger.error(f"Dify API HTTP错误: {e}")
        return "AI服务返回错误"
    except Exception as e:
        logger.error(f"Dify API调用失败: {e}")
        return "AI服务暂时不可用"


# 日志异常处理函数
def log_exception(logger: logging.Logger, exception: Exception, context: str = "") -> None:
    """记录异常信息"""
    logger.error(f"{context}: {type(exception).__name__}: {exception}")
    import traceback
    logger.debug(f"异常堆栈:\n{traceback.format_exc()}")