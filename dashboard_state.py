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
    ) -> None:
        self.source = source
        self.schema_version = schema_version
        self.stale_threshold_seconds = stale_threshold_seconds
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
        step = self._to_int(cache.get("Step"), default=0)
        total_steps = self._to_int(cache.get("TotalSteps"), default=0)
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
            controller_state=str(cache.get("state") or "Idle"),
            maa_status=str(cache.get("Status") or "Unknown"),
            current_user=str(cache.get("CurruentUser") or "Unknown"),
            next_user=str(cache.get("NextUser") or ""),
            step=step,
            total_steps=total_steps,
            progress_percent=self._compute_progress_percent(step, total_steps),
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

    def _is_online(self, *, current_time: float, last_update: float, connection: str) -> bool:
        if connection.lower() == "connected":
            return True
        if last_update <= 0:
            return False
        return (current_time - last_update) <= self.stale_threshold_seconds

    @staticmethod
    def _compute_progress_percent(step: int, total_steps: int) -> float:
        if total_steps <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (step / total_steps) * 100.0)), 2)

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
