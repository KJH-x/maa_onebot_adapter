"""
日志配置模块

为 MAA OneBot Adapter v2.1 提供统一的日志配置。
支持控制台和文件日志，支持日志轮转。
"""

import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any

from .type_definitions import ConfigDict


def setup_logging(
    config: Optional[ConfigDict] = None,
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    console_format: Optional[str] = None,
    file_format: Optional[str] = None
) -> logging.Logger:
    """
    设置统一的日志配置
    
    Args:
        config: 配置字典（如果提供，会从中读取日志配置）
        log_level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        log_file: 日志文件路径，None表示不记录到文件
        max_bytes: 单个日志文件最大字节数
        backup_count: 备份文件数量
        console_format: 控制台日志格式
        file_format: 文件日志格式
        
    Returns:
        配置好的根日志器
    """
    # 如果提供了配置，优先使用配置中的值
    if config:
        log_level = config.get("log_level", log_level)
        log_file = config.get("log_file", log_file)
        max_bytes = config.get("max_log_size", max_bytes)
        backup_count = config.get("log_backup_count", backup_count)
    
    # 日志级别映射
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    
    level = level_map.get(log_level.upper(), logging.INFO)
    
    # 配置根日志器
    logger = logging.getLogger()
    logger.setLevel(level)
    
    # 清除现有处理器（避免重复）
    logger.handlers.clear()
    
    # 默认日志格式
    if console_format is None:
        console_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    if file_format is None:
        file_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(console_format)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器（轮转）
    if log_file:
        try:
            # 确保日志目录存在
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(level)
            file_formatter = logging.Formatter(file_format)
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
            
            logger.info(f"文件日志已启用，路径: {log_file}")
        except Exception as e:
            logger.error(f"无法创建文件日志处理器: {e}")
            logger.warning("将继续使用控制台日志")
    
    # 设置常用模块的日志级别
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    logger.info(f"日志系统已初始化，级别: {log_level}")
    return logger


def get_logger(name: str = None) -> logging.Logger:
    """
    获取指定名称的日志器
    
    Args:
        name: 日志器名称，None表示根日志器
        
    Returns:
        日志器实例
    """
    return logging.getLogger(name)


def log_exception(logger: logging.Logger, exception: Exception, context: str = ""):
    """
    记录异常信息，包含堆栈跟踪
    
    Args:
        logger: 日志器
        exception: 异常对象
        context: 异常上下文描述
    """
    if context:
        logger.error(f"{context}: {exception}", exc_info=True)
    else:
        logger.error(f"发生异常: {exception}", exc_info=True)


def log_with_context(logger: logging.Logger, level: str, message: str, **context):
    """
    记录带上下文的日志
    
    Args:
        logger: 日志器
        level: 日志级别
        message: 日志消息
        **context: 上下文键值对
    """
    if context:
        context_str = " ".join(f"{k}={v}" for k, v in context.items())
        full_message = f"{message} [{context_str}]"
    else:
        full_message = message
    
    if level.upper() == "DEBUG":
        logger.debug(full_message)
    elif level.upper() == "INFO":
        logger.info(full_message)
    elif level.upper() == "WARNING":
        logger.warning(full_message)
    elif level.upper() == "ERROR":
        logger.error(full_message)
    elif level.upper() == "CRITICAL":
        logger.critical(full_message)
    else:
        logger.info(full_message)


def change_log_level(logger: logging.Logger, new_level: str):
    """
    动态修改日志级别
    
    Args:
        logger: 日志器
        new_level: 新的日志级别
    """
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    
    level = level_map.get(new_level.upper(), logging.INFO)
    logger.setLevel(level)
    
    # 更新所有处理器的级别
    for handler in logger.handlers:
        handler.setLevel(level)
    
    logger.info(f"日志级别已修改为: {new_level}")


# 预配置的日志器
def get_web_server_logger() -> logging.Logger:
    """获取Web服务器专用日志器"""
    return get_logger("web_server")


def get_ws_client_logger() -> logging.Logger:
    """获取WebSocket客户端专用日志器"""
    return get_logger("ws_client")


def get_dify_logger() -> logging.Logger:
    """获取Dify API专用日志器"""
    return get_logger("dify_api")


def get_config_logger() -> logging.Logger:
    """获取配置专用日志器"""
    return get_logger("config")


# 便捷函数
def setup_logging_from_config(config_path: str) -> logging.Logger:
    """
    从配置文件设置日志
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置好的日志器
    """
    try:
        from .config_validator import load_and_validate_config
        config = load_and_validate_config(config_path)
        return setup_logging(config=config)
    except Exception as e:
        # 如果配置加载失败，使用默认配置
        logger = setup_logging()
        logger.error(f"无法从配置文件加载日志配置: {e}")
        return logger