from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import time
from typing import Any

from system_telemetry import SystemTelemetryCollector


@dataclass
class DashboardState:
    schema_version: str
    source: str
    generated_at: float
    last_update: float
    online: bool
    controller_state: str
    maa_status: str
    current_user: str
    next_user: str
    step: int
    total_steps: int
    progress_percent: float
    execution_configs: list[str]
    progress_phase: str
    connection: str
    last_error: str | None
    telemetry: dict[str, Any]
    report_image_url: str | None
    screenshot_url: str | None


class DashboardStateBuilder:
    def __init__(
        self,
        source: str = "maa_onebot_adapter_v2.7",
        schema_version: str = "1.0",
        stale_threshold_seconds: float = 30.0,
        completed_reset_seconds: float = 8 * 60 * 60,
    ) -> None:
        self.source = source
        self.schema_version = schema_version
        self.stale_threshold_seconds = stale_threshold_seconds
        self.completed_reset_seconds = completed_reset_seconds
        self.telemetry_collector = SystemTelemetryCollector()

    def build(
        self,
        cache: dict[str, Any],
        *,
        report_image_url: str | None = None,
        screenshot_url: str | None = None,
        now: float | None = None,
    ) -> DashboardState:
        current_time = time.time() if now is None else now
        last_update = self._to_float(cache.get("lastUpdate"), default=0.0)
        execution_configs = self._normalize_execution_configs(cache.get("ExecutionConfigs"))
        total_steps = self._resolve_total_steps(cache.get("TotalSteps"), execution_configs)
        step = self._clamp_step(self._to_int(cache.get("Step"), default=0), total_steps)
        progress_phase = self._determine_progress_phase(cache, step=step, total_steps=total_steps)
        controller_state = str(cache.get("state") or "Idle")
        maa_status = str(cache.get("Status") or "Unknown")
        current_user = str(cache.get("CurruentUser") or "")
        next_user = str(cache.get("NextUser") or "")
        last_completed_at = self._to_float(cache.get("lastCompletedAt"), default=0.0)

        if self._should_reset_completed_state(
            current_time=current_time,
            progress_phase=progress_phase,
            last_completed_at=last_completed_at,
        ):
            progress_phase = "not_started"
            controller_state = "Idle"
            maa_status = "Idle"
            current_user = ""
            next_user = execution_configs[0] if execution_configs else ""
            step = 0
        elif progress_phase == "not_started" and not next_user and execution_configs:
            next_user = execution_configs[0]

        telemetry = self.telemetry_collector.collect()

        return DashboardState(
            schema_version=self.schema_version,
            source=self.source,
            generated_at=current_time,
            last_update=last_update,
            online=self._is_online(
                current_time=current_time,
                last_update=last_update,
                connection=str(cache.get("Connection") or ""),
            ),
            controller_state=controller_state,
            maa_status=maa_status,
            current_user=current_user,
            next_user=next_user,
            step=step,
            total_steps=total_steps,
            progress_percent=self._compute_progress_percent(
                step=step,
                total_steps=total_steps,
                progress_phase=progress_phase,
            ),
            execution_configs=execution_configs,
            progress_phase=progress_phase,
            connection=str(cache.get("Connection") or "Unknown"),
            last_error=self._normalize_error(cache.get("lastError")),
            telemetry=telemetry,
            report_image_url=report_image_url,
            screenshot_url=screenshot_url,
        )

    def to_dict(self, state: DashboardState) -> dict[str, Any]:
        return asdict(state)

    def compute_etag(self, state: DashboardState) -> str:
        payload = self.to_dict(state)
        payload.pop("generated_at", None)
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get_completed_reset_token(self, cache: dict[str, Any]) -> float | None:
        progress_phase = self._determine_progress_phase(
            cache,
            step=self._to_int(cache.get("Step"), default=0),
            total_steps=self._resolve_total_steps(
                cache.get("TotalSteps"),
                self._normalize_execution_configs(cache.get("ExecutionConfigs")),
            ),
        )
        if progress_phase != "completed":
            return None
        last_completed_at = self._to_float(cache.get("lastCompletedAt"), default=0.0)
        return last_completed_at if last_completed_at > 0 else None

    def get_seconds_until_completed_reset(
        self,
        cache: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float | None:
        last_completed_at = self.get_completed_reset_token(cache)
        if last_completed_at is None:
            return None
        current_time = time.time() if now is None else now
        due_at = last_completed_at + self.completed_reset_seconds
        remaining = due_at - current_time
        return max(0.0, remaining)

    def _is_online(self, *, current_time: float, last_update: float, connection: str) -> bool:
        lowered_connection = connection.lower()
        if lowered_connection == "connected":
            return True
        if lowered_connection in {"disconnected", "unreachable"}:
            return False
        if last_update <= 0:
            return False
        return (current_time - last_update) <= self.stale_threshold_seconds

    @staticmethod
    def _compute_progress_percent(*, step: int, total_steps: int, progress_phase: str) -> float:
        if progress_phase == "completed":
            return 100.0
        if progress_phase == "not_started":
            return 0.0
        if total_steps <= 0:
            return 0.0
        if step <= 0:
            return 0.0
        return round(max(0.0, min(100.0, ((step - 0.5) / total_steps) * 100.0)), 2)

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_error(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _clamp_step(step: int, total_steps: int) -> int:
        if total_steps <= 0:
            return max(0, step)
        return max(0, min(step, total_steps))

    @staticmethod
    def _resolve_total_steps(value: Any, execution_configs: list[str]) -> int:
        if execution_configs:
            return len(execution_configs)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _should_reset_completed_state(
        self,
        *,
        current_time: float,
        progress_phase: str,
        last_completed_at: float,
    ) -> bool:
        if progress_phase != "completed" or last_completed_at <= 0:
            return False
        return (current_time - last_completed_at) >= self.completed_reset_seconds

    @staticmethod
    def _determine_progress_phase(cache: dict[str, Any], *, step: int, total_steps: int) -> str:
        allowed_phases = {"not_started", "running", "completed", "failed", "stopped"}
        cached_phase = str(cache.get("progressPhase") or "").strip().lower()
        if cached_phase in allowed_phases:
            return cached_phase

        status = str(cache.get("Status") or "").strip()
        if status == "AllCompleted":
            return "completed"
        if status == "Failed":
            return "failed"
        if status == "ManuallyStopped":
            return "stopped"
        if status in {"Starting", "Idle"}:
            return "not_started"
        if status in {"Next_Step", "Running", "Reconnect"}:
            return "running" if step > 0 else "not_started"
        if step <= 0:
            return "not_started"
        if total_steps > 0 and step >= total_steps and cache.get("lastCompletedAt"):
            return "completed"
        return "running"

    @staticmethod
    def _normalize_execution_configs(value: Any) -> list[str]:
        items = value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            try:
                items = json.loads(stripped)
            except json.JSONDecodeError:
                items = [stripped]

        if not isinstance(items, list):
            return []

        return [str(item).strip() for item in items if str(item).strip()]
