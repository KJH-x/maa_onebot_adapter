"""
类型定义文件

为 MAA OneBot Adapter v2.1 提供类型提示支持。
所有类型定义保持与现有代码的完全兼容性。
"""

from typing import TypedDict, Optional, List, Dict, Any, Union


class DifyConfig(TypedDict):
    """Dify 配置类型定义"""
    api_key: str
    base_url: str
    timeout: int


class UserConfig(TypedDict):
    """用户配置类型定义"""
    user_id: int
    nickname: str
    required_at: bool
    message_type: str
    group_id: Optional[int]


class Message(TypedDict):
    """消息类型定义"""
    message_type: str
    user_id: int
    group_id: Optional[int]
    message: List[Dict[str, Any]]


class WebSocketMessage(TypedDict):
    """WebSocket 消息类型定义"""
    type: str
    data: Dict[str, Any]


class MAARequest(TypedDict):
    """MAA 请求类型定义"""
    uuid: str
    type: str
    params: Dict[str, Any]


class MAAResponse(TypedDict):
    """MAA 响应类型定义"""
    uuid: str
    type: str
    data: Dict[str, Any]
    error: Optional[str]


class ConfigDict(TypedDict):
    """配置文件类型定义"""
    dify_api_key: str
    dify_base_url: str
    port: int
    host: str
    log_level: str
    log_file: Optional[str]
    max_log_size: int
    log_backup_count: int


# 类型别名，便于使用
DifyResponse = Dict[str, Any]
EventPayload = Dict[str, Any]
ConnectionID = str
SessionID = str