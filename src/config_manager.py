"""
配置管理模块 - MAA OneBot Adapter v2.1

基于现有 config_validator.py 重构，提供统一的配置管理接口。
支持配置加载、验证、热重载和向后兼容。
"""

import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
import logging

from .type_definitions import ConfigDict


class ConfigManager:
    """
    配置管理器类
    
    提供统一的配置管理接口，支持：
    1. 配置加载和验证
    2. 配置热重载
    3. 向后兼容现有配置格式
    4. 安全的配置访问
    """
    
    def __init__(self, config_path: str = "config.json"):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径，默认为 "config.json"
        """
        self.config_path = Path(config_path)
        self.config: ConfigDict = {}
        self._last_modified: float = 0
        self._watchers: List[Callable[[ConfigDict], None]] = []
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_watching = threading.Event()
        self.logger = logging.getLogger(__name__)
        
        # 默认配置
        self.defaults: ConfigDict = {
            "ws_port": 8765,
            "host": "0.0.0.0",
            "log_level": "INFO",
            "log_file": None,
            "max_log_size": 10 * 1024 * 1024,  # 10MB
            "log_backup_count": 5
        }
    
    def load(self) -> bool:
        """
        加载配置文件
        
        Returns:
            bool: 加载是否成功
        """
        try:
            # 检查文件是否存在
            if not self.config_path.exists():
                self.logger.error(f"配置文件不存在: {self.config_path}")
                return False
            
            # 加载JSON配置
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 验证配置
            errors = self._validate_config(config)
            if errors:
                error_msg = "配置验证失败:\n" + "\n".join(f"  - {error}" for error in errors)
                self.logger.error(error_msg)
                return False
            
            # 应用默认值（仅当字段不存在时）
            for key, value in self.defaults.items():
                if key not in config:
                    config[key] = value
            
            self.config = config
            self._last_modified = self.config_path.stat().st_mtime
            self.logger.info(f"配置文件已加载: {self.config_path}")
            return True
            
        except json.JSONDecodeError as e:
            self.logger.error(f"配置文件JSON格式错误: {e}")
            return False
        except UnicodeDecodeError as e:
            self.logger.error(f"配置文件编码错误: {e}")
            return False
        except Exception as e:
            self.logger.error(f"加载配置文件时发生错误: {e}")
            return False
    
    def save(self) -> bool:
        """
        保存配置文件
        
        Returns:
            bool: 保存是否成功
        """
        try:
            # 验证当前配置
            errors = self._validate_config(self.config)
            if errors:
                error_msg = "配置验证失败，无法保存:\n" + "\n".join(f"  - {error}" for error in errors)
                self.logger.error(error_msg)
                return False
            
            # 保存到文件
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            
            self._last_modified = self.config_path.stat().st_mtime
            self.logger.info(f"配置文件已保存: {self.config_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存配置文件时发生错误: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        安全获取配置值
        
        Args:
            key: 配置键，支持点分隔符（如 "dify.api_key"）
            default: 默认值
            
        Returns:
            Any: 配置值或默认值
        """
        # 支持点分隔符访问嵌套配置
        if '.' in key:
            parts = key.split('.')
            value = self.config
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return default
            return value
        
        return self.config.get(key, default)
    
    def set(self, key: str, value: Any) -> bool:
        """
        设置配置值
        
        Args:
            key: 配置键，支持点分隔符
            value: 配置值
            
        Returns:
            bool: 设置是否成功
        """
        try:
            # 支持点分隔符设置嵌套配置
            if '.' in key:
                parts = key.split('.')
                config = self.config
                for i, part in enumerate(parts[:-1]):
                    if part not in config:
                        config[part] = {}
                    elif not isinstance(config[part], dict):
                        # 如果中间部分不是字典，创建新字典
                        config[part] = {}
                    config = config[part]
                config[parts[-1]] = value
            else:
                self.config[key] = value
            
            return True
        except Exception as e:
            self.logger.error(f"设置配置值时发生错误: {e}")
            return False
    
    def validate(self) -> bool:
        """
        验证当前配置
        
        Returns:
            bool: 配置是否有效
        """
        errors = self._validate_config(self.config)
        if errors:
            self.logger.warning(f"配置验证发现 {len(errors)} 个问题")
            for error in errors:
                self.logger.warning(f"  - {error}")
            return False
        return True
    
    def reload(self) -> bool:
        """
        重新加载配置文件
        
        Returns:
            bool: 重新加载是否成功
        """
        self.logger.info("重新加载配置文件...")
        return self.load()
    
    def watch_for_changes(self, callback: Callable[[ConfigDict], None]) -> None:
        """
        监听配置文件变化
        
        Args:
            callback: 配置变化时的回调函数
        """
        self._watchers.append(callback)
        
        # 如果还没有启动监听线程，启动一个
        if not self._watch_thread or not self._watch_thread.is_alive():
            self._stop_watching.clear()
            self._watch_thread = threading.Thread(
                target=self._watch_config_file,
                daemon=True
            )
            self._watch_thread.start()
            self.logger.info("配置文件变化监听已启动")
    
    def stop_watching(self) -> None:
        """停止监听配置文件变化"""
        self._stop_watching.set()
        if self._watch_thread and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=2)
        self.logger.info("配置文件变化监听已停止")
    
    def get_all(self) -> ConfigDict:
        """
        获取所有配置
        
        Returns:
            ConfigDict: 完整的配置字典
        """
        return self.config.copy()
    
    def update(self, new_config: Dict[str, Any]) -> bool:
        """
        批量更新配置
        
        Args:
            new_config: 新的配置字典
            
        Returns:
            bool: 更新是否成功
        """
        try:
            # 创建临时配置用于验证（合并当前配置和新配置）
            temp_config = self.config.copy()
            temp_config.update(new_config)
            
            # 验证合并后的配置
            errors = self._validate_config(temp_config)
            if errors:
                error_msg = "配置更新验证失败:\n" + "\n".join(f"  - {error}" for error in errors)
                self.logger.error(error_msg)
                return False
            
            # 更新配置
            self.config.update(new_config)
            self.logger.info("配置已批量更新")
            return True
        except Exception as e:
            self.logger.error(f"批量更新配置时发生错误: {e}")
            return False
    
    # 私有方法
    def _validate_config(self, config: Dict[str, Any]) -> List[str]:
        """
        验证配置并返回错误列表
        
        Args:
            config: 配置字典
            
        Returns:
            List[str]: 错误消息列表，空列表表示验证通过
        """
        errors = []
        
        # 必需字段检查
        required_fields = ["dify_api_key", "dify_base_url"]
        for field in required_fields:
            if field not in config:
                errors.append(f"缺少必需字段: {field}")
            elif not config[field]:
                errors.append(f"字段 {field} 不能为空")
        
        # Dify配置验证
        if "dify_api_key" in config and config["dify_api_key"]:
            api_key = config["dify_api_key"]
            if not isinstance(api_key, str):
                errors.append("Dify API密钥必须是字符串")
            elif not api_key.startswith("app-"):
                errors.append("Dify API密钥格式不正确，应以 'app-' 开头")
        
        # WebSocket端口验证
        if "ws_port" in config:
            ws_port = config["ws_port"]
            if not isinstance(ws_port, int):
                errors.append("WebSocket端口号必须是整数")
            elif ws_port < 1 or ws_port > 65535:
                errors.append(f"WebSocket端口号无效: {ws_port} (范围: 1-65535)")
        
        # 主机验证
        if "host" in config:
            host = config["host"]
            if not isinstance(host, str):
                errors.append("主机地址必须是字符串")
            elif not host:
                errors.append("主机地址不能为空")
        
        # 日志级别验证
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if "log_level" in config:
            log_level = config["log_level"]
            if not isinstance(log_level, str):
                errors.append("日志级别必须是字符串")
            elif log_level.upper() not in valid_log_levels:
                errors.append(f"无效的日志级别: {log_level} (有效值: {', '.join(valid_log_levels)})")
        
        # 日志文件路径验证
        if "log_file" in config and config["log_file"]:
            log_file = config["log_file"]
            if not isinstance(log_file, str):
                errors.append("日志文件路径必须是字符串")
            else:
                # 检查目录是否存在
                log_dir = os.path.dirname(log_file)
                if log_dir and not os.path.exists(log_dir):
                    errors.append(f"日志文件目录不存在: {log_dir}")
        
        # 日志大小验证
        if "max_log_size" in config:
            max_log_size = config["max_log_size"]
            if not isinstance(max_log_size, int):
                errors.append("最大日志大小必须是整数")
            elif max_log_size < 1024 * 1024:  # 小于1MB
                errors.append(f"最大日志大小太小: {max_log_size} (最小: 1MB)")
        
        # 日志备份数量验证
        if "log_backup_count" in config:
            backup_count = config["log_backup_count"]
            if not isinstance(backup_count, int):
                errors.append("日志备份数量必须是整数")
            elif backup_count < 0:
                errors.append(f"日志备份数量不能为负数: {backup_count}")
        
        return errors
    
    def _watch_config_file(self) -> None:
        """
        监听配置文件变化的内部方法
        """
        check_interval = 5  # 每5秒检查一次
        
        while not self._stop_watching.is_set():
            try:
                if self.config_path.exists():
                    current_mtime = self.config_path.stat().st_mtime
                    
                    # 如果文件被修改
                    if current_mtime > self._last_modified:
                        self.logger.info("检测到配置文件变化，重新加载...")
                        
                        # 尝试重新加载
                        if self.load():
                            # 通知所有观察者
                            for watcher in self._watchers:
                                try:
                                    watcher(self.config)
                                except Exception as e:
                                    self.logger.error(f"配置变化回调执行失败: {e}")
                        else:
                            self.logger.warning("配置文件重新加载失败，保持当前配置")
                    
                    self._last_modified = current_mtime
                
            except Exception as e:
                self.logger.error(f"监听配置文件时发生错误: {e}")
            
            # 等待下一次检查
            time.sleep(check_interval)


# 向后兼容的函数
def load_and_validate_config(path: str) -> ConfigDict:
    """
    向后兼容的函数：加载并验证配置文件
    
    Args:
        path: 配置文件路径
        
    Returns:
        ConfigDict: 验证通过的配置字典
        
    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置文件格式错误或验证失败
    """
    manager = ConfigManager(path)
    if not manager.load():
        raise ValueError("配置加载失败")
    return manager.get_all()


def validate_config(config: Dict[str, Any]) -> List[str]:
    """
    向后兼容的函数：验证配置
    
    Args:
        config: 配置字典
        
    Returns:
        List[str]: 错误消息列表
    """
    manager = ConfigManager()
    return manager._validate_config(config)


def get_config_value(config: ConfigDict, key: str, default: Any = None) -> Any:
    """
    向后兼容的函数：安全获取配置值
    
    Args:
        config: 配置字典
        key: 配置键
        default: 默认值
        
    Returns:
        Any: 配置值或默认值
    """
    return config.get(key, default)


def validate_dify_config(api_key: str, base_url: str) -> List[str]:
    """
    向后兼容的函数：验证Dify配置
    
    Args:
        api_key: Dify API密钥
        base_url: Dify基础URL
        
    Returns:
        List[str]: 错误消息列表
    """
    errors = []
    
    if not api_key:
        errors.append("Dify API密钥不能为空")
    elif not isinstance(api_key, str):
        errors.append("Dify API密钥必须是字符串")
    elif not api_key.startswith("app-"):
        errors.append("Dify API密钥格式不正确，应以 'app-' 开头")
    
    if not base_url:
        errors.append("Dify基础URL不能为空")
    elif not isinstance(base_url, str):
        errors.append("Dify基础URL必须是字符串")
    elif not base_url.startswith(("http://", "https://")):
        errors.append("Dify基础URL必须以 http:// 或 https:// 开头")
    
    return errors