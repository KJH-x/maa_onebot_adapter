"""
MAA OneBot Adapter v2.1 - 改进版

此包包含改进后的核心模块：
- type_definitions: 类型定义
- config_manager: 配置管理（包含验证功能）
- logging_config: 日志配置
"""

__version__ = "2.1.0"
__author__ = "MAA OneBot Adapter Team"

# 导出公共接口
from .type_definitions import (
    DifyConfig,
    UserConfig,
    Message,
    WebSocketMessage,
    MAARequest,
    MAAResponse,
    ConfigDict,
    DifyResponse,
    EventPayload,
    ConnectionID,
    SessionID
)

from .config_manager import (
    ConfigManager,
    validate_config,
    load_and_validate_config,
    get_config_value,
    validate_dify_config
)

from .logging_config import (
    setup_logging,
    get_logger,
    log_exception,
    log_with_context,
    change_log_level,
    get_web_server_logger,
    get_ws_client_logger,
    get_dify_logger,
    get_config_logger,
    setup_logging_from_config
)

__all__ = [
    # 类型定义
    "DifyConfig",
    "UserConfig",
    "Message",
    "WebSocketMessage",
    "MAARequest",
    "MAAResponse",
    "ConfigDict",
    "DifyResponse",
    "EventPayload",
    "ConnectionID",
    "SessionID",
    
    # 配置管理
    "ConfigManager",
    "validate_config",
    "load_and_validate_config",
    "get_config_value",
    "validate_dify_config",
    
    # 日志配置
    "setup_logging",
    "get_logger",
    "log_exception",
    "log_with_context",
    "change_log_level",
    "get_web_server_logger",
    "get_ws_client_logger",
    "get_dify_logger",
    "get_config_logger",
    "setup_logging_from_config",
]