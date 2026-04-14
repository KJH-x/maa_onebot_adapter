"""
日志系统模块 - MAA OneBot Adapter v2.1

基于现有 logging_config.py 重构，提供统一的日志接口。
支持结构化日志、多级别日志、文件轮转等功能。
"""

import logging
import sys
import os
import json
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any, Union, List
from datetime import datetime

from .type_definitions import ConfigDict


class StructuredLogger:
    """结构化日志记录器"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
    
    def debug(self, message: str, **context):
        """记录DEBUG级别结构化日志"""
        self._log_with_context("DEBUG", message, **context)
    
    def info(self, message: str, **context):
        """记录INFO级别结构化日志"""
        self._log_with_context("INFO", message, **context)
    
    def warning(self, message: str, **context):
        """记录WARNING级别结构化日志"""
        self._log_with_context("WARNING", message, **context)
    
    def error(self, message: str, **context):
        """记录ERROR级别结构化日志"""
        self._log_with_context("ERROR", message, **context)
    
    def critical(self, message: str, **context):
        """记录CRITICAL级别结构化日志"""
        self._log_with_context("CRITICAL", message, **context)
    
    def _log_with_context(self, level: str, message: str, **context):
        """内部方法：记录带上下文的日志"""
        if context:
            # 结构化日志格式：消息 + JSON上下文
            context_json = json.dumps(context, ensure_ascii=False)
            full_message = f"{message} | {context_json}"
        else:
            full_message = message
        
        if level == "DEBUG":
            self.logger.debug(full_message)
        elif level == "INFO":
            self.logger.info(full_message)
        elif level == "WARNING":
            self.logger.warning(full_message)
        elif level == "ERROR":
            self.logger.error(full_message)
        elif level == "CRITICAL":
            self.logger.critical(full_message)
    
    def exception(self, exc: Exception, message: str = "", **context):
        """记录异常信息"""
        if message:
            full_message = f"{message}: {exc}"
        else:
            full_message = f"发生异常: {exc}"
        
        if context:
            context_json = json.dumps(context, ensure_ascii=False)
            full_message = f"{full_message} | {context_json}"
        
        self.logger.error(full_message, exc_info=True)


class LoggingSystem:
    """日志系统主类"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LoggingSystem, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.root_logger = None
            self._structured_loggers = {}
            self._initialized = True
    
    def setup(
        self,
        config: Optional[ConfigDict] = None,
        log_level: str = "INFO",
        log_file: Optional[str] = None,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        console_format: Optional[str] = None,
        file_format: Optional[str] = None,
        enable_json_logging: bool = False
    ) -> logging.Logger:
        """
        设置日志系统
        
        Args:
            config: 配置字典
            log_level: 日志级别
            log_file: 日志文件路径
            max_bytes: 单个日志文件最大字节数
            backup_count: 备份文件数量
            console_format: 控制台日志格式
            file_format: 文件日志格式
            enable_json_logging: 是否启用JSON格式日志
            
        Returns:
            配置好的根日志器
        """
        # 如果提供了配置，优先使用配置中的值
        if config:
            log_level = config.get("log_level", log_level)
            log_file = config.get("log_file", log_file)
            max_bytes = config.get("max_log_size", max_bytes)
            backup_count = config.get("log_backup_count", backup_count)
            enable_json_logging = config.get("enable_json_logging", enable_json_logging)
        
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
        self.root_logger = logging.getLogger()
        self.root_logger.setLevel(level)
        
        # 清除现有处理器（避免重复）
        self.root_logger.handlers.clear()
        
        # 默认日志格式
        if console_format is None:
            if enable_json_logging:
                console_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            else:
                console_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        
        if file_format is None:
            if enable_json_logging:
                file_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
            else:
                file_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
        
        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(console_format)
        console_handler.setFormatter(console_formatter)
        self.root_logger.addHandler(console_handler)
        
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
                self.root_logger.addHandler(file_handler)
                
                self.root_logger.info(f"文件日志已启用，路径: {log_file}")
            except Exception as e:
                self.root_logger.error(f"无法创建文件日志处理器: {e}")
                self.root_logger.warning("将继续使用控制台日志")
        
        # 设置常用模块的日志级别
        logging.getLogger("websockets").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        
        self.root_logger.info(f"日志系统已初始化，级别: {log_level}")
        return self.root_logger
    
    def get_logger(self, name: str = None) -> logging.Logger:
        """
        获取标准日志器
        
        Args:
            name: 日志器名称，None表示根日志器
            
        Returns:
            日志器实例
        """
        if name is None:
            return self.root_logger if self.root_logger else logging.getLogger()
        return logging.getLogger(name)
    
    def get_structured_logger(self, name: str = None) -> StructuredLogger:
        """
        获取结构化日志器
        
        Args:
            name: 日志器名称，None表示根日志器
            
        Returns:
            结构化日志器实例
        """
        if name is None:
            logger = self.root_logger if self.root_logger else logging.getLogger()
        else:
            logger = logging.getLogger(name)
        
        # 缓存结构化日志器
        cache_key = name or "root"
        if cache_key not in self._structured_loggers:
            self._structured_loggers[cache_key] = StructuredLogger(logger)
        
        return self._structured_loggers[cache_key]
    
    def change_log_level(self, new_level: str, logger_name: str = None):
        """
        动态修改日志级别
        
        Args:
            new_level: 新的日志级别
            logger_name: 日志器名称，None表示根日志器
        """
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        
        level = level_map.get(new_level.upper(), logging.INFO)
        
        if logger_name is None:
            logger = self.root_logger if self.root_logger else logging.getLogger()
        else:
            logger = logging.getLogger(logger_name)
        
        logger.setLevel(level)
        
        # 更新所有处理器的级别
        for handler in logger.handlers:
            handler.setLevel(level)
        
        logger.info(f"日志级别已修改为: {new_level}")
    
    def log_exception(self, logger: logging.Logger, exception: Exception, context: str = ""):
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


# 全局日志系统实例
_logging_system = LoggingSystem()


# 兼容性函数（保持与旧代码兼容）
def setup_logging(
    config: Optional[ConfigDict] = None,
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console_format: Optional[str] = None,
    file_format: Optional[str] = None
) -> logging.Logger:
    """兼容性函数：设置日志配置"""
    return _logging_system.setup(
        config=config,
        log_level=log_level,
        log_file=log_file,
        max_bytes=max_bytes,
        backup_count=backup_count,
        console_format=console_format,
        file_format=file_format
    )


def get_logger(name: str = None) -> logging.Logger:
    """兼容性函数：获取指定名称的日志器"""
    return _logging_system.get_logger(name)


def get_structured_logger(name: str = None) -> StructuredLogger:
    """获取结构化日志器"""
    return _logging_system.get_structured_logger(name)


def log_exception(logger: logging.Logger, exception: Exception, context: str = ""):
    """兼容性函数：记录异常信息"""
    _logging_system.log_exception(logger, exception, context)


def change_log_level(logger: logging.Logger, new_level: str):
    """兼容性函数：动态修改日志级别"""
    # 从日志器名称推断
    logger_name = logger.name if logger.name != "root" else None
    _logging_system.change_log_level(new_level, logger_name)


# 预配置的日志器（保持兼容）
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


# 新增：性能监控日志
def get_performance_logger() -> logging.Logger:
    """获取性能监控专用日志器"""
    return get_logger("performance")


# 新增：审计日志
def get_audit_logger() -> logging.Logger:
    """获取审计专用日志器"""
    return get_logger("audit")


# 新增：业务日志
def get_business_logger() -> logging.Logger:
    """获取业务专用日志器"""
    return get_logger("business")