import json
import logging.handlers
import os
from typing import Any

# 导入新的日志配置
from src.logging_system import get_config_logger


class ColoredFormatter(logging.Formatter):
    """自定义的日志格式化类，用于在控制台输出带颜色的日志。"""

    COLOR_CODES = {
        logging.DEBUG: '[90m',  # 蓝色
        logging.INFO: '[92m',  # 绿色
        logging.WARNING: '[93m',  # 黄色
        logging.ERROR: '[91m',  # 红色
        # logging.CRITICAL: '[41m[97m'  # 背景红色，字体白色
    }
    RESET_CODE = '[0m'  # 重置颜色

    def format(self, record: logging.LogRecord) -> str:
        log_message = super().format(record)
        return self.COLOR_CODES.get(record.levelno, '') + log_message + self.RESET_CODE


def setup_logging(log_file_path: str = './log/app.log', log_level: int = logging.INFO):
    """配置并初始化日志系统。"""

    log_dir = os.path.dirname(p=log_file_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger('app')
    logger.setLevel(log_level)

    if not logger.handlers:
        # 文件处理器，不带颜色
        file_handler = logging.handlers.RotatingFileHandler(
            log_file_path,
            maxBytes=1024*1024*5,
            backupCount=5,
            encoding='utf-8'
        )
        file_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)-8s][%(module)s:%(lineno)3d]|%(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        # 控制台处理器，带颜色
        console_handler = logging.StreamHandler()
        colored_formatter = ColoredFormatter(
            '%(message)s'
        )
        console_handler.setFormatter(colored_formatter)
        logger.addHandler(console_handler)

    return logger


def load_config(config_path: str = "config.json") -> dict[str, Any]:
    """
    读取配置文件 config.json。
    如果文件不存在或格式错误，将抛出异常。

    参数:
    config_path: 配置文件路径，默认为当前目录下的 config.json。

    返回:
    - 成功读取时返回配置字典。
    """
    logger = get_config_logger()
    
    if not os.path.exists(config_path):
        logger.error(f"配置文件不存在: {config_path}")
        raise FileNotFoundError(f"❌ 配置文件不存在: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config: dict[str, Any] = json.load(f)
            logger.info(f"配置文件已加载: {config_path}")
            return config

    except json.JSONDecodeError as e:
        logger.error(f"配置文件解析错误（JSON格式不正确）: {e}")
        raise ValueError(f"❌ 配置文件解析错误（JSON格式不正确）: {e}")


def write_config(data: dict[str, Any], config_path: str = "cache.json"):
    logger = get_config_logger()
    
    try:
        with open(config_path, "w") as f:
            json.dump(obj=data, fp=f)
        logger.debug(f"配置已写入文件: {config_path}")

    except json.JSONDecodeError as e:
        logger.error(f"配置文件解析错误（JSON格式不正确）: {e}")
        raise ValueError(f"❌ 配置文件解析错误（JSON格式不正确）: {e}")
    except Exception as e:
        logger.error(f"写入配置文件时发生错误: {e}")
        raise


def load_cache(cache_path: str = "cache.json") -> dict[str, Any]:
    """
    读取缓存文件 cache.json。
    如果文件不存在，返回空字典。

    参数:
    cache_path: 缓存文件路径，默认为当前目录下的 cache.json。

    返回:
    - 成功读取时返回缓存字典，文件不存在时返回空字典。
    """
    logger = get_config_logger()
    
    if not os.path.exists(cache_path):
        logger.info(f"缓存文件不存在，返回空字典: {cache_path}")
        return {}
    
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache: dict[str, Any] = json.load(f)
            logger.debug(f"缓存文件已加载: {cache_path}")
            return cache
    except json.JSONDecodeError as e:
        logger.error(f"缓存文件解析错误（JSON格式不正确）: {e}")
        raise ValueError(f"❌ 缓存文件解析错误（JSON格式不正确）: {e}")
