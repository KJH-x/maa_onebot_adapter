import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

import websockets

CONFIG_FILE = "./loop/config.json"
STARTUP_LOCK = asyncio.Lock()

# 此脚本是maa控制端（不成熟版本）暂未给出使用方法


class ExecutionStatus(Enum):
    """执行状态枚举"""
    IDLE = "Idle"
    STARTING = "Starting"
    RUNNING = "Running"
    NEXT_STEP = "Next_Step"
    ALL_COMPLETED = "AllCompleted"
    FAILED = "Failed"
    RECONNECT = "Reconnect"

    # Deprecated status:
    #   "Starting",
    #   "GotoNext",
    #   "Finished",
    #   "Completed/NotRunning",
    #   "ManuallyStopped",


@dataclass
class ExecutionState:
    """执行状态管理，用于在主执行循环和 WebSocket 管理器之间共享状态"""
    current_step: int = 0
    total_steps: int = 0
    status: ExecutionStatus = ExecutionStatus.IDLE
    current_user: Optional[str] = None
    next_user: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    change_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def update(self,
                     current_step: Optional[int] = None,
                     total_steps: Optional[int] = None,
                     status: Optional[ExecutionStatus] = None,
                     current_user: Optional[str] = None,
                     next_user: Optional[str] = None) -> None:
        """原子性更新状态"""
        async with self.lock:
            if current_step is not None:
                self.current_step = current_step
            if total_steps is not None:
                self.total_steps = total_steps
            if status is not None:
                self.status = status
            if current_user is not None:
                self.current_user = current_user
            if next_user is not None:
                self.next_user = next_user
            # 触发事件，通知汇报器状态已更新
            self.change_event.set()
            print("[DEBUG] 状态更新发出")

    async def wait_for_change(self, timeout: Optional[float] = None) -> None:
        """等待状态变化"""
        try:
            self.change_event.clear()
            await asyncio.wait_for(self.change_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def get_snapshot(self) -> dict[str, Any]:
        """获取当前状态的快照"""
        async with self.lock:
            return {
                "current_step": self.current_step,
                "total_steps": self.total_steps,
                "status": self.status.value,
                "current_user": self.current_user,
                "next_user": self.next_user,
            }


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


async def wait_for_done(timeout: Optional[float] = None) -> None:
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
        print(" ⚠️ 等待 done.flag 超时")

    # 在检测到 done.flag 之后，给子进程短时间退出的机会
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        # 子进程仍在运行，继续无需阻塞
        print(" ⚠️ 子进程在 done.flag 创建后仍在运行，继续执行。")

    duration = int(time.time() - start_time)
    minutes, seconds = divmod(duration, 60)
    print(f"参数 {param} 的任务（{index}/{total}）完成。耗时：{minutes:02d}:{seconds:02d}")


async def main_execution_loop(execution_state: ExecutionState, config_list: List[str]) -> None:
    """
    主执行循环，独立于 WebSocket 连接，负责执行 MAA 任务。

    参数:
        execution_state: 共享的执行状态对象
        config_list: 配置文件列表
    """
    total_steps = len(config_list)

    print("=" * 60)
    print("主执行循环启动")
    print("=" * 60)

    await STARTUP_LOCK.acquire()
    await execution_state.update(
        total_steps=total_steps,
        status=ExecutionStatus.STARTING
    )
    while STARTUP_LOCK.locked():
        print("[DEBUG] wait STARTUP_LOCK")
        await asyncio.sleep(3)

    try:
        # 清理旧的 done.flag
        if os.path.exists(DONE_FLAG):
            os.remove(DONE_FLAG)

        for idx, config in enumerate(config_list):
            current_step = idx + 1
            next_config = config_list[idx + 1] if idx + 1 < total_steps else None

            # 更新状态：进入该步骤
            await execution_state.update(
                current_step=current_step,
                current_user=config,
                next_user=next_config,
                status=ExecutionStatus.NEXT_STEP
            )

            print(f"\n--- 步骤 {current_step}/{total_steps} ---")
            print(f"开始处理: {config}")

            # 执行 MAA 任务（不关心 WebSocket 连接状态）
            await run_MAA(config, current_step, total_steps)

        # 所有步骤完成
        await execution_state.update(
            current_step=total_steps,
            current_user=config_list[-1] if total_steps > 0 else None,
            next_user=None,
            status=ExecutionStatus.ALL_COMPLETED
        )

        print("\n" + "=" * 60)
        print("✅ 所有任务执行完成")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 主执行循环发生错误: {e}")
        await execution_state.update(status=ExecutionStatus.FAILED)
        raise


async def websocket_manager(url: str, token: str, execution_state: ExecutionState,
                            ws_connected_event: asyncio.Event, shutdown_event: asyncio.Event) -> None:
    """
    WebSocket 连接管理器，独立于主执行循环。
    负责连接/重连、心跳保活、以及消费执行状态并发送汇报。

    参数:
        url: WebSocket 服务器地址
        token: 认证令牌
        execution_state: 共享的执行状态对象
        ws_connected_event: 连接成功信号
        shutdown_event: 关闭信号
    """
    retry_delay = 0.5  # 初始重试延迟（秒）
    max_retry_delay = 5.0  # 最大重试延迟（秒）
    keepalive_interval = 5.0  # 心跳间隔（秒）

    custom_headers: dict[str, str] = {
        'User-Agent': 'MaaCtrl/00',
        'Authorization': f'Bearer {token}',
    }

    print(f"[WebSocket 管理器] 初始化完成，将连接到: {url}")

    while not shutdown_event.is_set():
        try:
            print(f"[WebSocket 管理器] 尝试连接到服务器...")

            async with websockets.connect(url, additional_headers=custom_headers) as websocket:
                print("✅ [WebSocket 管理器] 连接成功")
                ws_connected_event.set()
                retry_delay = 0.5  # 重置重试延迟

                # 启动心跳任务
                async def keepalive():
                    try:
                        while not shutdown_event.is_set():
                            await asyncio.sleep(keepalive_interval)
                            try:
                                await websocket.ping()
                            except Exception as e:
                                print(f" ⚠️ [WebSocket 管理器] Keepalive ping 失败: {e}")
                                raise  # 触发重连
                    except asyncio.CancelledError:
                        return

                keepalive_task = asyncio.create_task(keepalive())

                # 启动报告发送器（消费状态更新）
                async def report_sender():
                    print("[DEBUG] report_sender activated")
                    try:
                        last_snapshot = None
                        while not shutdown_event.is_set():
                            # 等待状态变化或超时
                            await execution_state.wait_for_change(timeout=5.0)

                            # 获取当前状态快照
                            snapshot = await execution_state.get_snapshot()

                            # 只在状态真正改变时发送
                            if snapshot != last_snapshot:
                                report_body = {
                                    "CurruentUser": snapshot["current_user"],
                                    "NextUser": snapshot["next_user"],
                                    "Status": snapshot["status"],
                                    "Connection": "Connected",
                                    "Step": str(snapshot["current_step"]) if snapshot["current_step"] > 0 else None,
                                    "TotalSteps": str(snapshot["total_steps"])
                                }

                                try:
                                    report_json = json.dumps(report_body)
                                    await websocket.send(report_json)
                                    print(f"[WebSocket 管理器] 发送汇报: {report_body['Status']}")
                                    if STARTUP_LOCK.locked():
                                        STARTUP_LOCK.release()
                                        print("[DEBUG] STARTUP_LOCK released")
                                    last_snapshot = snapshot
                                except Exception as e:
                                    print(f" ⚠️ [WebSocket 管理器] 发送汇报失败: {e}")
                                    raise  # 触发重连
                    except asyncio.CancelledError:
                        return

                if STARTUP_LOCK.locked():
                    STARTUP_LOCK.release()
                    print("[DEBUG] STARTUP_LOCK released")
                reporter_task = asyncio.create_task(report_sender())

                try:
                    # 等待关闭信号或任何连接错误
                    await asyncio.gather(keepalive_task, reporter_task)
                except Exception as e:
                    print(f" ⚠️ [WebSocket 管理器] 连接出现错误: {e}")
                finally:
                    keepalive_task.cancel()
                    reporter_task.cancel()
                    try:
                        await keepalive_task
                    except asyncio.CancelledError:
                        pass
                    try:
                        await reporter_task
                    except asyncio.CancelledError:
                        pass

                    ws_connected_event.clear()

        except (ConnectionRefusedError, websockets.exceptions.WebSocketException) as e:
            ws_connected_event.clear()
            if shutdown_event.is_set():
                break
            print(f"❌ [WebSocket 管理器] 连接失败: {e}")
            print(f"[WebSocket 管理器] {retry_delay:.1f} 秒后重试...")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=retry_delay)
                break
            except asyncio.TimeoutError:
                pass

            # 指数退避
            retry_delay = min(retry_delay * 1.5, max_retry_delay)
            execution_state.status = ExecutionStatus.RECONNECT

        except Exception as e:
            ws_connected_event.clear()
            print(f"❌ [WebSocket 管理器] 发生意外错误: {e}")
            if shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=retry_delay)
                break
            except asyncio.TimeoutError:
                pass

    print("[WebSocket 管理器] 关闭完成")


async def main_async(ws_url: str, token: str, config_list: List[str]) -> None:
    """
    异步主函数，协调主执行循环和 WebSocket 管理器。

    参数:
        ws_url: WebSocket 服务器地址
        token: 认证令牌
        config_list: 配置文件列表
    """
    # 创建共享状态和事件
    execution_state = ExecutionState()
    ws_connected_event = asyncio.Event()
    shutdown_event = asyncio.Event()
    ws_manager_task = None

    try:
        # 启动 WebSocket 管理器任务（后台运行）
        await STARTUP_LOCK.acquire()
        ws_manager_task = asyncio.create_task(
            websocket_manager(ws_url, token, execution_state, ws_connected_event, shutdown_event)
        )
        while STARTUP_LOCK.locked():
            print("[DEBUG] wait STARTUP_LOCK")
            await asyncio.sleep(3)

        # 启动主执行循环（前台运行，会阻塞直到完成）
        try:
            await main_execution_loop(execution_state, config_list)
        except Exception as e:
            print(f"\n❌ 主执行循环失败: {e}")

        # 等待一段时间让最后的汇报发送
        print("\n等待 WebSocket 汇报完成...")
        try:
            await asyncio.wait_for(asyncio.sleep(3), timeout=3)
        except asyncio.TimeoutError:
            pass

        # 发送关闭信号
        shutdown_event.set()

        # 等待 WebSocket 管理器关闭
        try:
            await asyncio.wait_for(ws_manager_task, timeout=10)
        except asyncio.TimeoutError:
            print(" ⚠️ WebSocket 管理器关闭超时")
            ws_manager_task.cancel()

    except KeyboardInterrupt:
        print("\n\n⏹️ 程序被用户中断")
        shutdown_event.set()
        if ws_manager_task:
            try:
                await asyncio.wait_for(ws_manager_task, timeout=5)
            except asyncio.TimeoutError:
                ws_manager_task.cancel()
        raise


async def stop_report(url: str, token: str):
    custom_headers: dict[str, str] = {
        'User-Agent': 'MaaCtrl/00',
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
        asyncio.run(main_async(
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
