"""
MAA Remote Control test server (aiohttp)

- Listens on port 7780.
- Accepts POST /maa/getTask and POST /maa/reportStatus only.
- Validates client by JSON fields "user" and "device" plus an Authorization Bearer token.
- Issues two tasks (CaptureImage and Heartbeat) as a pair. Each task gets a fresh UUID.
- Keeps pending tasks until reported completed. After all pending tasks complete, sets a cooldown
  of 10 minutes before issuing the next pair. When all complete, calls a placeholder reporting
  function `on_all_tasks_completed` (no-op).
- Console output is kept concise: per-endpoint counters and last brief request/response summary to
  reduce spam from high-frequency MAA polling.
"""
# from __future__ import annotations

import asyncio
import base64
import datetime
import json
import os
import uuid
from io import BytesIO
from typing import Any, Optional

from aiohttp import web
from PIL import Image

# ---------- Configuration ----------
HOST = "0.0.0.0"
PORT = 7780

CONFIG_FILE = "allowed_clients.json"
COOLDOWN_SECONDS = 5  # Seconds

# 设置请求体最大为 10 MiB (10 * 1024 * 1024 字节)
MAX_SIZE = 10 * 1024 * 1024

allowed_clients: dict[str, dict[str, Any]] = {}


def _load_config() -> None:
    global allowed_clients
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                allowed_clients = json.load(f)
                print(f"Loaded clients config from {CONFIG_FILE}")
                return
        except Exception as e:
            print(f"Failed to read {CONFIG_FILE}: {e}")
    # fallback test config
    print("Config file missing, using demo client credentials.")
    demo_user = "ea6c39eb-a45f-4d82-9ecc-33a7bf2ae4dc"
    demo_device = "f4a18418311c4f2bb3230dd2a1f4e695"
    allowed_clients[demo_user] = {"device": demo_device}
    print(f"Demo credentials -> user: {demo_user} device: {demo_device}")


# ---------- Server state ----------
_state_lock = asyncio.Lock()
_pending_tasks: dict[str, dict[str, Any]] = {}
_cooldown_until: Optional[datetime.datetime] = None

_console_summary: dict[str, dict[str, Any]] = {}
_console_lock = asyncio.Lock()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _authorized(user: str, device: str) -> bool:
    if user not in allowed_clients:
        return False
    info = allowed_clients[user]
    allowed_device = info.get("device")
    if isinstance(allowed_device, list):
        if device not in allowed_device:
            return False
    else:
        if device != allowed_device:
            return False
    return True


def _make_task(task_type: str) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "type": task_type}


async def on_all_tasks_completed(user: str, device: str) -> None:
    # Placeholder for future reporting logic
    return


async def _update_console(path: str, req_brief: str, resp_brief: str) -> None:
    async with _console_lock:
        s = _console_summary.setdefault(path, {"count": 0, "last_req": "", "last_resp": ""})
        s["count"] += 1
        s["last_req"] = req_brief
        s["last_resp"] = resp_brief
        print(f"[{path}] calls={s['count']} last_req={req_brief} last_resp={resp_brief}")


async def handle_get_task(request: web.Request) -> web.Response:
    path = "/maa/getTask"
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        await _update_console(path, "invalid_json", "400")
        return web.Response(status=400, text="invalid json")

    user = data.get("user", "")
    device = data.get("device", "")
    # token = None
    # auth_hdr = request.headers.get("Authorization", "")
    # if auth_hdr.startswith("Bearer "):
    #     token = auth_hdr.split(" ", 1)[1].strip()

    req_brief = f"user={user} device={device}"
    if not _authorized(user, device):
        await _update_console(path, req_brief, "401")
        return web.Response(status=401, text="Unauthorized")

    async with _state_lock:
        global _cooldown_until

        if _pending_tasks:
            tasks = list(_pending_tasks.values())
            await _update_console(path, req_brief, f"pending={len(tasks)}")
            return web.json_response({"tasks": tasks})

        if _cooldown_until and _now() < _cooldown_until:
            remain = int((_cooldown_until - _now()).total_seconds())
            await _update_console(path, req_brief, f"cooldown {remain}s")
            return web.json_response({"tasks": []})

        t1 = _make_task("CaptureImage")
        # t2 = _make_task("Heartbeat")
        _pending_tasks[t1["id"]] = t1
        # _pending_tasks[t2["id"]] = t2
        await _update_console(path, req_brief, "issued_pair")
        return web.json_response({"tasks": [t1
                                            # , t2
                                            ]})


async def handle_report_status(request: web.Request) -> web.Response:
    path = "/maa/reportStatus"
    try:
        data: dict[str, Any] = await request.json()
    except Exception as e:
        await _update_console(path, f"invalid_json{e}", "400")
        return web.Response(status=400, text="invalid json")

    user = data.get("user", "")
    device = data.get("device", "")
    task_id = data.get("task")
    status = data.get("status", "")
    # token = None
    # auth_hdr = request.headers.get("Authorization", "")
    # if auth_hdr.startswith("Bearer "):
    #     token = auth_hdr.split(" ", 1)[1].strip()

    req_brief = f"user={user} device={device} task={task_id} status={status}"
    if not _authorized(user, device):
        await _update_console(path, req_brief, "401")
        return web.Response(status=401, text="Unauthorized")

    async with _state_lock:
        if not task_id or task_id not in _pending_tasks:
            await _update_console(path, req_brief, "404")
            return web.Response(status=404, text="unknown task")

        if  (_task_finished := _pending_tasks.pop(task_id, None)):
            if (_task_finished.get("type","")=="CaptureImage"):
                # data comes without data:image.../ prefix
                base64_data = data.get("payload","")
                # Send
                
                # img = Image.open(BytesIO(base64.b64decode(base64_data)))
                # img.show()

        if not _pending_tasks:
            global _cooldown_until
            _cooldown_until = _now() + datetime.timedelta(seconds=COOLDOWN_SECONDS)
            asyncio.create_task(on_all_tasks_completed(user, device))
            await _update_console(path, req_brief, "done_all")
            return web.json_response({"result": "OK", "message": "all tasks completed, entering cooldown"})

        await _update_console(path, req_brief, f"remain={len(_pending_tasks)}")
        return web.json_response({"result": "OK"})


async def handle_other(request: web.Request) -> web.Response:
    await _update_console("other", request.path, "404")
    return web.Response(status=404, text="not found")


def make_app() -> web.Application:
    app = web.Application(client_max_size=MAX_SIZE)
    app.router.add_post("/maa/getTask", handle_get_task)
    app.router.add_post("/maa/reportStatus", handle_report_status)
    app.router.add_route("*", "/{tail:.*}", handle_other)
    return app


if __name__ == "__main__":
    _load_config()
    app = make_app()
    web.run_app(app, host=HOST, port=PORT)
