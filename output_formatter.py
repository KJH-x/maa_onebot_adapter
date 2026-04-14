"""
输出格式化模块 - 使用现成库优化命令行和日志输出

依赖:
    pip install rich tabulate
"""

import logging
import sys
from typing import Any, Dict, List, Optional
from datetime import datetime

# Rich 库导入
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.logging import RichHandler
from rich.theme import Theme
from rich.highlighter import RegexHighlighter

# Tabulate 库导入
from tabulate import tabulate


# ============ 1. Rich Console 主题配置 ============

CUSTOM_THEME = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "debug": "dim white",
    "highlight": "magenta",
    "label": "bold blue",
})

# 全局 Console 实例
console = Console(theme=CUSTOM_THEME, force_terminal=True)


# ============ 2. 自定义日志处理器 (使用 Rich) ============

class MAAFormatter(logging.Formatter):
    """简洁的日志格式器"""
    
    def format(self, record: logging.LogRecord) -> str:
        # 简化的标签格式
        level_tag = record.levelname[:4]  # INFO, WARN, ERRO, DEBU
        return f"[{level_tag}] {record.getMessage()}"


def setup_rich_logging(log_level: int = logging.INFO) -> logging.Logger:
    """
    配置使用 Rich 的日志系统
    
    特点:
    - 彩色输出
    - 自动时间戳
    - 更好的异常追踪
    - 同时写入文件
    """
    import os
    from logging.handlers import RotatingFileHandler
    
    # 创建日志目录
    log_dir = "log"
    os.makedirs(log_dir, exist_ok=True)
    
    # 配置文件处理器
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)-8s][%(name)-12s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    # 配置 Rich 控制台处理器
    rich_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        show_time=True,
        show_path=False,
    )
    rich_handler.setLevel(log_level)
    
    # 配置根日志
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[rich_handler, file_handler]
    )
    
    # 设置第三方库的日志级别
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    
    return logging.getLogger("maa_adapter")


# ============ 3. 状态报告格式化 (使用 Rich Table) ============

def format_status_report_rich(
    current_user: str,
    next_user: str,
    connection: str,
    status: str,
    latency_onebot: Optional[float] = None,
    latency_maa: Optional[float] = None
) -> str:
    """
    使用 Rich 格式化状态报告
    
    返回渲染后的字符串，可用于发送到 QQ
    """
    # 创建表格
    table = Table(
        title="📊 MAA 状态报告",
        box=box.ROUNDED,
        show_header=False,
        border_style="cyan",
    )
    
    table.add_column("项目", style="label", justify="right")
    table.add_column("值", style="white")
    
    # 添加行
    table.add_row("📋 当前配置", current_user)
    table.add_row("⏭️ 下一配置", next_user)
    table.add_row("🔗 WS连接", connection)
    table.add_row("⚙️ 控制器状态", status)
    
    if latency_onebot is not None:
        table.add_row("🐧 OneBot延迟", f"{latency_onebot*1000:.1f}ms")
    if latency_maa is not None:
        table.add_row("⚙️ MAA延迟", f"{latency_maa*1000:.1f}ms" if latency_maa > 0 else "⛓️‍💥 断开")
    
    # 捕获输出为字符串
    with console.capture() as capture:
        console.print(table)
    return capture.get()


def format_help_message_rich() -> str:
    """使用 Rich 格式化帮助信息"""
    table = Table(
        title="🤖 MAA OneBot 助手",
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold magenta",
    )
    
    table.add_column("命令", style="cyan", justify="left")
    table.add_column("别名", style="dim")
    table.add_column("功能描述", style="white")
    
    commands = [
        ("help", "-", "显示本帮助信息"),
        ("测试", "test", "测试回复状态"),
        ("ws状态", "ws", "查询WebSocket连接状态"),
        ("现在", "currentuser", "查询当前执行的配置用户"),
        ("下一个", "nextuser", "查询下一个待执行用户"),
        ("控制器", "host", "查询MAA和控制器状态"),
        ("report", "-", "显示详细状态报告"),
    ]
    
    for cmd, alias, desc in commands:
        table.add_row(cmd, alias, desc)
    
    with console.capture() as capture:
        console.print(table)
        console.print("\n[dim]提示词: MAA (大小写通用)[/dim]")
    return capture.get()


# ============ 4. 使用 Tabulate 的纯文本格式化 ============

def format_status_report_tabulate(
    current_user: str,
    next_user: str,
    connection: str,
    status: str,
    latency_onebot: Optional[float] = None,
    latency_maa: Optional[float] = None
) -> str:
    """
    使用 Tabulate 格式化状态报告 (纯文本，兼容 QQ)
    """
    data = [
        ["📋 当前配置", current_user],
        ["⏭️ 下一配置", next_user],
        ["🔗 WS连接", connection],
        ["⚙️ 控制器状态", status],
    ]
    
    if latency_onebot is not None:
        data.append(["🐧 OneBot延迟", f"{latency_onebot*1000:.1f}ms"])
    if latency_maa is not None:
        status_text = f"{latency_maa*1000:.1f}ms" if latency_maa > 0 else "⛓️‍💥 断开"
        data.append(["⚙️ MAA延迟", status_text])
    
    table = tabulate(data, tablefmt="simple_outline", colalign=("left", "left"))
    return f"📊 MAA 状态报告\n{table}"


def format_help_message_tabulate() -> str:
    """使用 Tabulate 格式化帮助信息"""
    data = [
        ["help", "-", "显示本帮助信息"],
        ["测试", "test", "测试回复状态"],
        ["ws状态", "ws", "查询WebSocket连接状态"],
        ["现在", "currentuser", "查询当前执行的配置用户"],
        ["下一个", "nextuser", "查询下一个待执行用户"],
        ["控制器", "host", "查询MAA和控制器状态"],
        ["report", "-", "显示详细状态报告"],
    ]
    
    table = tabulate(
        data,
        headers=["命令", "别名", "功能描述"],
        tablefmt="simple",
        colalign=("left", "left", "left")
    )
    
    return f"🤖 MAA OneBot 助手\n\n{table}\n\n提示词: MAA (大小写通用)"


def format_connection_status_tabulate(
    latency_onebot: float,
    latency_maa: Optional[float] = None,
    maa_connected: bool = True
) -> str:
    """格式化连接状态"""
    data = [
        ["🐧 OneBot", f"{latency_onebot*1000:.1f}ms"],
    ]
    
    if maa_connected and latency_maa is not None:
        data.append(["⚙️ MAA控制器", f"{latency_maa*1000:.1f}ms (已连接)"])
    else:
        data.append(["⚙️ MAA控制器", "⛓️‍💥 断开"])
    
    table = tabulate(data, tablefmt="simple", colalign=("left", "left"))
    return f"📡 连接状态\n{table}"


def format_batch_task_start_tabulate(total_steps: int, delay_seconds: int = 60) -> str:
    """格式化批量任务启动消息"""
    data = [
        ["⏰ 预计开始", f"{delay_seconds}秒内"],
        ["📊 任务数量", f"{total_steps} 个账号"],
        ["📋 状态", "排队中..."],
    ]
    
    table = tabulate(data, tablefmt="simple", colalign=("left", "left"))
    return f"🚀 MAA 批量任务启动\n{table}"


def format_batch_task_complete_tabulate(total_steps: int, duration_minutes: float) -> str:
    """格式化批量任务完成消息"""
    data = [
        ["📊 完成任务", f"{total_steps} 个账号"],
        ["⏱️ 总耗时", f"{duration_minutes:.2f} 分钟"],
    ]
    
    table = tabulate(data, tablefmt="simple", colalign=("left", "left"))
    return f"✅ MAA 批量任务完成\n{table}"


# ============ 5. 通知消息格式化 ============

def format_next_step_notification(user: str, last_user: Optional[str] = None, duration: Optional[float] = None) -> str:
    """
    格式化下一步通知
    
    返回适合发送到 QQ 的文本
    """
    lines = [
        f"⏰ 即将开始运行 MAA 一键长草",
        f"👤 用户: {user}",
        f"⏳ 预计开始时间: 20秒后",
        f"💡 提示: 请勿操作鼠标键盘",
    ]
    
    if last_user and duration is not None:
        lines.append(f"\n✅ {last_user} 已完成, 耗时 {duration:.2f} 分钟")
    
    return "\n".join(lines)


# ============ 6. 日志标签统一化 ============

class LogTag:
    """统一的日志标签 - 固定宽度8字符（含括号）"""
    WS_SERVER = "[WSServer]"      # 8字符
    MSG_ONEBOT = "[MsgOneB]"      # 8字符 (缩短)
    MSG_MAA = "[MsgMAA  ]"        # 8字符 (补空格)
    MSG_MAIN = "[MsgMain ]"       # 8字符 (补空格)
    CHECK_CONN = "[ChkConn ]"     # 8字符 (缩短)
    BROADCAST = "[Bdcst   ]"      # 8字符 (缩短)
    DIFY = "[Dify    ]"           # 8字符 (补空格)
    CONFIG = "[Config  ]"         # 8字符 (补空格)
    LAUNCHER = "[Launcher]"       # 8字符


def format_log_message(tag: str, message: str, level: str = "INFO") -> str:
    """格式化日志消息，统一标签格式"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    return f"[{timestamp}] {tag} {message}"


# ============ 7. 使用示例和测试 ============

if __name__ == "__main__":
    # 测试 Rich 输出
    print("=" * 50)
    print("Rich 格式化示例:")
    print("=" * 50)
    
    # 设置日志
    logger = setup_rich_logging(logging.DEBUG)
    logger.info("这是一个信息日志")
    logger.warning("这是一个警告日志")
    logger.error("这是一个错误日志")
    
    # 状态报告
    print("\n" + format_status_report_rich(
        current_user="User1",
        next_user="User2",
        connection="Connected",
        status="Running",
        latency_onebot=0.015,
        latency_maa=0.025
    ))
    
    # 帮助信息
    print("\n" + format_help_message_rich())
    
    print("\n" + "=" * 50)
    print("Tabulate 格式化示例 (适合发送到 QQ):")
    print("=" * 50)
    
    # 纯文本格式 (适合发送到 QQ)
    print("\n" + format_status_report_tabulate(
        current_user="User1",
        next_user="User2",
        connection="Connected",
        status="Running",
        latency_onebot=0.015,
        latency_maa=0.025
    ))
    
    print("\n" + format_help_message_tabulate())
    
    print("\n" + format_connection_status_tabulate(
        latency_onebot=0.015,
        latency_maa=0.025,
        maa_connected=True
    ))
    
    print("\n" + format_batch_task_start_tabulate(total_steps=5))
    
    print("\n" + format_batch_task_complete_tabulate(
        total_steps=5,
        duration_minutes=12.5
    ))
    
    print("\n" + format_next_step_notification(
        user="User2",
        last_user="User1",
        duration=2.5
    ))
