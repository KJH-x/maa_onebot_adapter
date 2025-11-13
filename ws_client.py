import asyncio
import json
import os
import time
from typing import Any, List, Optional

import websockets
from websockets.asyncio.client import ClientConnection

CONFIG_FILE = "./loop/config.json"


def load_config() -> tuple[str, list[str], str, str]:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config: dict[str, Any] = json.load(f)

    required_keys = {
        "maa": str,
        "cfg_list": list,
        "WS_URL": str,
        "TOKEN": str
    }

    missing = [k for k in required_keys if k not in config or config[k] in (None, "")]
    if missing:
        raise ValueError(f"Missing {', '.join(missing)} in {CONFIG_FILE}")

    # 类型验证
    for key, expected_type in required_keys.items():
        if not isinstance(config[key], expected_type):
            raise TypeError(f"'{key}' in {CONFIG_FILE} must be of type {expected_type.__name__}.")

    # 检查 cfg_list 内部类型
    cfg_list = config["cfg_list"]
    if not all(isinstance(item, str) for item in cfg_list):
        bad_index = next(i for i, item in enumerate(cfg_list) if not isinstance(item, str))
        raise TypeError(f"Item at index {bad_index} in 'cfg_list' must be a string.")

    return (
        config["maa"],
        cfg_list,
        config["WS_URL"],
        config["TOKEN"]
    )


DONE_FLAG = "./loop/done.flag"
MAA, CFG_LIST, WS_URL, TOKEN = load_config()


async def main_ws(url: str, token: str, config_list: List[Any]) -> None:
    """
    连接WebSocket，执行耗时流程，并在每一步汇报状态。

    参数:
        url: WebSocket连接地址。
        token: 用于Authorization Header的Bearer Token。
        config_list: 流程步骤配置列表。
        delay: 模拟每一步骤的耗时（秒）。
    """
    async def wait_for_done(timeout: float | None = None) -> None:
        """等待核心程序完成（通过检测 done.flag 文件）。

        如果提供 timeout（秒），在超时后抛出 asyncio.TimeoutError。
        """
        print("等待核心程序完成...")

        async def _poll_done() -> None:
            while not os.path.exists(DONE_FLAG):
                await asyncio.sleep(2)
            print("检测到完成信号。")
            try:
                os.remove(DONE_FLAG)
            except Exception:
                pass

        if timeout is None:
            await _poll_done()
        else:
            await asyncio.wait_for(_poll_done(), timeout=timeout)

    async def run_MAA(param: str, index: int, total: int) -> None:
        """运行 MAA 程序并等待完成（第 index 个，共 total 个）。

        通过 asyncio.create_subprocess_exec 启动子进程，避免阻塞主事件循环。检测到 done.flag 后，尝试等待子进程短时间退出。
        """
        start_time = time.time()

        try:
            process = await asyncio.create_subprocess_exec(
                MAA, '--config', param,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            print(f"❌ 找不到可执行文件 MAA: {MAA}")
            return
        except Exception as e:
            print(f"❌ 启动 MAA 失败: {e}")
            return

        # 等待 done.flag；如果需要，也可以对 wait_for_done 加超时
        try:
            await wait_for_done()
        except asyncio.TimeoutError:
            print("⚠️ 等待 done.flag 超时")

        # 在检测到 done.flag 之后，给子进程短时间退出的机会
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            # 子进程仍在运行，继续无需阻塞
            print("⚠️ 子进程在 done.flag 创建后仍在运行，继续执行。")

        duration = int(time.time() - start_time)
        minutes, seconds = divmod(duration, 60)
        print(f"参数 {param} 的任务（{index}/{total}）完成。耗时：{minutes:02d}:{seconds:02d}")

    # 1. 构建WebSocket连接所需的Header
    custom_headers: dict[str, str] = {
        'User-Agent': 'MaaReport/00',
        'Authorization': f'Bearer {token}',
    }

    print(f"尝试连接到WebSocket服务器: {url}")
    try:
        os.remove(DONE_FLAG) if os.path.exists(DONE_FLAG) else None
        total = len(CFG_LIST)
        idx = 0
        async with websockets.connect(url, additional_headers=custom_headers) as websocket:
            print("✅ WebSocket连接成功。")

            # 启动一个 keepalive（心跳）任务，定期发送 ping，防止连接因长时间无数据而被服务端断开。
            async def _keepalive(ws: ClientConnection, interval: float = 20.0):
                try:
                    while True:
                        await asyncio.sleep(interval)
                        try:
                            await ws.ping()
                        except Exception as e:
                            # 记录 ping 错误，循环会在连接关闭时退出
                            print(f"⚠️ keepalive ping 失败: {e}")
                except asyncio.CancelledError:
                    return

            keepalive_task = asyncio.create_task(_keepalive(websocket, interval=20.0))

            total_steps: int = len(config_list)
            # 开始汇报
            start_report: dict[str, Optional[str]] = {
                "CurruentUser": None,
                "NextUser": config_list[0] if total_steps else None,
                "Status": "Starting",
                "Connection": "Established",
                "Step": None,
                "TotalSteps": f"{total_steps}"
            }

            start_report_json: str = json.dumps(start_report)
            print(f"发送开始汇报: {start_report_json}")
            try:
                await websocket.send(start_report_json)
            except Exception as e:
                print(f"❌ 发送开始汇报失败: {e}")

            for idx in range(total_steps):
                current_user: Any = config_list[idx]
                next_user: Optional[Any] = config_list[idx + 1] if idx + 1 < total_steps else None

                report_body: dict[str, Optional[str]] = {
                    "CurruentUser": f"{current_user}",
                    "NextUser": f"{next_user}",
                    "Status": "GotoNext",
                    "Connection": "Connected",
                    "Step": f"{idx + 1}",
                    "TotalSteps": f"{total_steps}"
                }

                report_json: str = json.dumps(report_body)

                print(f"\n--- 步骤 {idx + 1}/{total_steps} ---")
                print(f"开始处理: {current_user}, 发送更新汇报")
                try:
                    await websocket.send(report_json)
                except Exception as e:
                    print(f"❌ 发送状态更新到服务器失败: {e}")
                    # 如果发送失败，继续执行本地任务，但需注意连接可能已关闭

                # 运行 MAA（异步方式）并等待 done.flag，不会阻塞事件循环
                await run_MAA(current_user, idx, total)

            # 结束汇报
            end_report: dict[str, Optional[str]] = {
                "CurruentUser": config_list[-1] if total_steps else None,
                "NextUser": None,
                "Status": "Finished",
                "Connection": "Closing",
                "Step": f"{total_steps}",
                "TotalSteps": f"{total_steps}"
            }

            end_report_json: str = json.dumps(end_report)
            print(f"发送结束汇报: {end_report_json}")
            try:
                await websocket.send(end_report_json)
            except Exception as e:
                print(f"❌ 发送结束汇报失败: {e}")

            # 5. 流程结束后汇报结束状态
            final_report_body: dict[str, Optional[str]] = {
                "CurruentUser": None,
                "NextUser": None,
                "Status": "Completed/NotRunning",
                "Connection": "Unreachable",
                "Step": f"{total_steps}",
                "TotalSteps": f"{total_steps}"
            }

            final_report_json: str = json.dumps(final_report_body)
            print("\n--- 流程结束 ---")
            print(f"发送最终汇报: {final_report_json}")
            try:
                await websocket.send(final_report_json)
            except Exception as e:
                print(f"❌ 发送最终汇报失败: {e}")

            print("✅ WebSocket连接即将自动断开。")

            # 结束时取消 keepalive 任务
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass

    except ConnectionRefusedError:
        print(f"❌ 连接失败: 无法连接到服务器 {url}。请检查服务器是否运行且地址正确。")
    except websockets.exceptions.InvalidURI:
        print(f"❌ 连接失败: URI格式错误 {url}。")
    except websockets.exceptions.InvalidMessage:
        print("❌ 消息发送失败: 消息格式无效。")
    except Exception as e:
        print(f"❌ 发生意外错误: {e}")


async def stop_report(url: str, token: str):
    custom_headers: dict[str, str] = {
        'User-Agent': 'MaaReport/00',
        'Authorization': f'Bearer {token}',
    }
    async with websockets.connect(url, additional_headers=custom_headers) as websocket:
        final_report_body: dict[str, Optional[str]] = {
            "CurruentUser": None,
            "NextUser": None,
            "Status": "ManuallyStopped",
            "Connection": "Unreachable",
            "Step": "-1",
            "TotalSteps": "-1"
        }
        try:
            await websocket.send(json.dumps(final_report_body))
        except Exception as e:
            print(f"❌ 发送最终汇报失败: {e}")

# --- 主入口点 ---
if __name__ == "__main__":
    try:
        asyncio.run(main_ws(
            WS_URL,
            TOKEN,
            CFG_LIST,
        ))
    except KeyboardInterrupt:
        print("\n程序被用户中断。")
        try:
            asyncio.run(stop_report(
                WS_URL,
                TOKEN,
            ))
        except Exception as e:
            print(e)

    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            print("Asyncio运行时错误: 事件循环在程序结束前被关闭。")
        else:
            raise
