from __future__ import annotations

import abc
import asyncio
from io import BytesIO
import json
from pathlib import Path
from typing import Any, Callable

from botocore.session import get_session

from dashboard_state import DashboardStateBuilder


class PublishTarget(abc.ABC):
    @abc.abstractmethod
    async def publish_json(self, relative_path: str, payload: bytes) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    async def publish_file(self, relative_path: str, payload: bytes, content_type: str) -> str:
        raise NotImplementedError


class LocalSnapshotTarget(PublishTarget):
    def __init__(self, base_dir: str = "dashboard_snapshot") -> None:
        self.base_dir = Path(base_dir)

    async def publish_json(self, relative_path: str, payload: bytes) -> str:
        return await self.publish_file(relative_path, payload, "application/json")

    async def publish_file(self, relative_path: str, payload: bytes, content_type: str) -> str:
        path = self.base_dir / Path(relative_path)
        await asyncio.to_thread(self._write_bytes, path, payload)
        return Path(relative_path).as_posix()

    @staticmethod
    def _write_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


class R2PublishTarget(PublishTarget):
    def __init__(
        self,
        *,
        bucket_name: str,
        endpoint: str,
        access_key_id: str,
        secret_access_key: str,
        public_base_url: str | None = None,
        region_name: str = "auto",
    ) -> None:
        self.bucket_name = bucket_name
        self.endpoint = endpoint.rstrip("/")
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self.client = get_session().create_client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

    async def publish_json(self, relative_path: str, payload: bytes) -> str:
        return await self.publish_file(relative_path, payload, "application/json; charset=utf-8")

    async def publish_file(self, relative_path: str, payload: bytes, content_type: str) -> str:
        key = Path(relative_path).as_posix()
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket_name,
            Key=key,
            Body=payload,
            ContentType=content_type,
            CacheControl="no-store, max-age=0",
        )
        if self.public_base_url:
            return f"{self.public_base_url}/{key}"
        return key


class DashboardPublisher:
    def __init__(
        self,
        *,
        state_builder: DashboardStateBuilder,
        publish_target: PublishTarget,
        cache_provider: Callable[[], dict[str, Any]],
        report_image_provider: Callable[[], Any | None],
        screenshot_image_provider: Callable[[], Any | None],
        logger: Any,
        interval: float = 2.0,
        publish_report_image: bool = True,
        publish_screenshot: bool = False,
    ) -> None:
        self.state_builder = state_builder
        self.publish_target = publish_target
        self.cache_provider = cache_provider
        self.report_image_provider = report_image_provider
        self.screenshot_image_provider = screenshot_image_provider
        self.logger = logger
        self.interval = interval
        self.publish_report_image = publish_report_image
        self.publish_screenshot = publish_screenshot

        self._dirty = True
        self._change_counter = 1
        self._published_counter = 0
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._last_state_etag: str | None = None
        self._last_report_digest: str | None = None
        self._last_screenshot_digest: str | None = None
        self._last_completed_reset_token: float | None = None
        self._observed_completed_reset_token: float | None = None

    def mark_dirty(self) -> None:
        self._dirty = True
        self._change_counter += 1
        self._wake_event.set()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._wake_event.set()
        self._task = asyncio.create_task(self._run(), name="dashboard-publisher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                cache = self.cache_provider()
                current_reset_token = self.state_builder.get_completed_reset_token(cache)
                current_change_counter = self._change_counter
                needs_event_publish = current_change_counter != self._published_counter
                if current_reset_token != self._observed_completed_reset_token:
                    self._observed_completed_reset_token = current_reset_token
                    self._last_completed_reset_token = None

                due_reset_token = None
                seconds_until_reset = self.state_builder.get_seconds_until_completed_reset(cache)
                if seconds_until_reset is not None and seconds_until_reset <= 0:
                    due_reset_token = current_reset_token

                if needs_event_publish or (
                    due_reset_token is not None
                    and due_reset_token != self._last_completed_reset_token
                ):
                    await self.publish_if_needed(
                        cache,
                        report_image=self.report_image_provider(),
                        screenshot_image=self.screenshot_image_provider(),
                    )
                    if needs_event_publish:
                        self._published_counter = current_change_counter
                    if due_reset_token is not None:
                        self._last_completed_reset_token = due_reset_token
                    self._dirty = self._change_counter != self._published_counter
            except Exception as exc:
                self.logger.error(f"Dashboard publish failed: {exc}", exc_info=True)

            cache = self.cache_provider()
            timeout = self.state_builder.get_seconds_until_completed_reset(cache)
            current_reset_token = self.state_builder.get_completed_reset_token(cache)
            if (
                timeout is not None
                and timeout <= 0
                and current_reset_token == self._last_completed_reset_token
            ):
                timeout = None
            await self._wait_for_trigger(timeout)

    async def publish_if_needed(
        self,
        cache: dict[str, Any],
        *,
        report_image: Any | None = None,
        screenshot_image: Any | None = None,
    ) -> bool:
        report_payload = self._serialize_image(report_image) if self.publish_report_image else None
        screenshot_payload = self._serialize_image(screenshot_image) if self.publish_screenshot else None

        report_url = "latest/report.png" if report_payload else None
        screenshot_url = "latest/screenshot.png" if screenshot_payload else None

        state = self.state_builder.build(
            cache,
            report_image_url=report_url,
            screenshot_url=screenshot_url,
        )
        state_etag = self.state_builder.compute_etag(state)

        report_digest = self._compute_digest(report_payload)
        screenshot_digest = self._compute_digest(screenshot_payload)

        has_state_change = state_etag != self._last_state_etag
        has_report_change = report_digest != self._last_report_digest
        has_screenshot_change = screenshot_digest != self._last_screenshot_digest

        if not any([has_state_change, has_report_change, has_screenshot_change]):
            return False

        if has_report_change and report_payload:
            await self.publish_target.publish_file("latest/report.png", report_payload, "image/png")

        if has_screenshot_change and screenshot_payload:
            await self.publish_target.publish_file("latest/screenshot.png", screenshot_payload, "image/png")

        status_payload = json.dumps(
            self.state_builder.to_dict(state),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        await self.publish_target.publish_json("latest/status.json", status_payload)

        self._last_state_etag = state_etag
        self._last_report_digest = report_digest
        self._last_screenshot_digest = screenshot_digest
        self.logger.info("Dashboard snapshot published")
        return True

    async def _wait_for_trigger(self, timeout: float | None) -> None:
        if self._dirty or self._change_counter != self._published_counter or self._stop_event.is_set():
            return
        self._wake_event.clear()
        wait_tasks = [
            asyncio.create_task(self._stop_event.wait()),
            asyncio.create_task(self._wake_event.wait()),
        ]
        try:
            done, pending = await asyncio.wait(
                wait_tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                return
            await asyncio.gather(*done, return_exceptions=True)
        finally:
            for task in wait_tasks:
                if not task.done():
                    task.cancel()

    @staticmethod
    def _serialize_image(image: Any | None) -> bytes | None:
        if image is None:
            return None
        if isinstance(image, bytes):
            return image
        if hasattr(image, "save"):
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        raise TypeError(f"Unsupported image payload type: {type(image)!r}")

    @staticmethod
    def _compute_digest(payload: bytes | None) -> str | None:
        if payload is None:
            return None

        import hashlib

        return hashlib.sha256(payload).hexdigest()
