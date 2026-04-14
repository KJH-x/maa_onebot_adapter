import argparse
import asyncio
import logging
import os
import sys
from typing import Any

import websockets

from dashboard_publisher import DashboardPublisher, LocalSnapshotTarget, R2PublishTarget
from dashboard_runtime import DashboardRuntime
from dashboard_state import DashboardStateBuilder
from src.logging_system import setup_logging
from websocket_server import WebSocketServer
from http_server import process_request
from src.config_manager import ConfigManager, load_and_validate_config
from output_formatter import setup_rich_logging, LogTag

# 修复Windows控制台编码问题
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


async def main(config: dict[str, Any], port_config: dict[str, int], host: str = "0.0.0.0"):

    server = WebSocketServer(config)
    dashboard_runtime: DashboardRuntime | None = None
    dashboard_config: dict[str, Any] = config.get("dashboard_publish", {})

    if dashboard_config.get("enabled", False):
        state_builder = DashboardStateBuilder(
            source="maa_onebot_adapter_v2.7",
            stale_threshold_seconds=float(dashboard_config.get("stale_threshold_seconds", 30.0)),
        )
        publish_target_name = str(dashboard_config.get("target", "local")).lower()

        if publish_target_name == "r2":
            r2_config: dict[str, Any] = dashboard_config.get("r2", {})
            access_key_id = str(
                r2_config.get("access_key_id")
                or os.getenv("R2_ACCESS_KEY_ID")
                or ""
            )
            secret_access_key = str(
                r2_config.get("secret_access_key")
                or os.getenv("R2_SECRET_ACCESS_KEY")
                or ""
            )
            bucket_name = str(r2_config.get("bucket_name") or "")
            endpoint = str(r2_config.get("endpoint") or "")

            if not all([bucket_name, endpoint, access_key_id, secret_access_key]):
                raise ValueError("R2 publish target selected but required R2 config is incomplete")

            publish_target = R2PublishTarget(
                bucket_name=bucket_name,
                endpoint=endpoint,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                public_base_url=str(r2_config.get("public_base_url") or "").strip() or None,
            )
        else:
            publish_target = LocalSnapshotTarget(
                base_dir=str(dashboard_config.get("local_dir", "dashboard_snapshot"))
            )

        publisher = DashboardPublisher(
            state_builder=state_builder,
            publish_target=publish_target,
            cache_provider=server.get_dashboard_cache,
            report_image_provider=server.get_latest_report_image,
            screenshot_image_provider=server.get_latest_screenshot_image,
            logger=main_logger,
            interval=float(dashboard_config.get("interval", 2.0)),
            publish_report_image=bool(dashboard_config.get("publish_report_image", True)),
            publish_screenshot=bool(dashboard_config.get("publish_screenshot", False)),
        )
        dashboard_runtime = DashboardRuntime(server, publisher, main_logger)
        server.attach_dashboard_runtime(dashboard_runtime)
        await dashboard_runtime.start()
        main_logger.info(f"{LogTag.WS_SERVER} Dashboard发布已启用 | 目标: {publish_target_name}")
    # http_server = create_http_app('/maaReport')

    try:
        async with websockets.serve(server.handler, host, port_config["ws"],
                                    process_request=process_request):
            # async with AioHttpServerWrapper(http_server, host, port_config["http"]):

                main_logger.info(f"{LogTag.WS_SERVER} 🚀 服务器启动 | WebSocket端口: {port_config['ws']}")
                await asyncio.Future()
    finally:
        if dashboard_runtime is not None:
            await dashboard_runtime.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行 WebSocket 和 HTTP 服务器。")
    parser.add_argument(
        "--loglevel",
        "-l",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="设置日志级别 (debug, info, warning, error)",
    )
    args = parser.parse_args()
    input_log_level:str = args.loglevel.lower()

    # 使用 Rich 日志配置系统
    main_logger = setup_rich_logging(log_level=getattr(logging, input_log_level.upper()))
    main_logger.info(f'{LogTag.WS_SERVER} 📝 日志系统初始化 | 级别: {input_log_level.upper()}')
    
    try:
        # 使用新的配置管理器加载配置
        config_manager = ConfigManager("config.json")
        if not config_manager.load():
            main_logger.error(f'{LogTag.CONFIG} ❌ 配置文件加载失败')
            sys.exit(1)
        
        main_logger.info(f'{LogTag.CONFIG} ✅ 配置加载成功 | 文件: config.json')
        
        # 获取配置
        local_config = config_manager.get_all()
        
        # 从配置中获取端口设置，如果没有则使用默认值
        ws_port = config_manager.get("ws_port", 8765)
        port_config = {'ws': ws_port}
        
        main_logger.info(f'{LogTag.CONFIG} 📡 端口配置 | WebSocket: {ws_port}')
        
        asyncio.run(main(local_config, port_config))
    except FileNotFoundError as e:
        main_logger.error(f'{LogTag.CONFIG} ❌ 配置文件错误: {e}')
        sys.exit(1)
    except ValueError as e:
        main_logger.error(f'{LogTag.CONFIG} ❌ 配置验证失败: {e}')
        sys.exit(1)
    except KeyboardInterrupt:
        main_logger.info(f"{LogTag.WS_SERVER} 🛑 用户中断 (Ctrl+C) | 正在退出...")
