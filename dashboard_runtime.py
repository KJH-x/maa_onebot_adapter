from __future__ import annotations

from typing import Any

from dashboard_publisher import DashboardPublisher


class DashboardRuntime:
    def __init__(self, server: Any, publisher: DashboardPublisher, logger: Any) -> None:
        self.server = server
        self.publisher = publisher
        self.logger = logger

    async def start(self) -> None:
        await self.publisher.start()
        self.publisher.mark_dirty()
        self.logger.info("Dashboard runtime started")

    async def stop(self) -> None:
        await self.publisher.stop()
        self.logger.info("Dashboard runtime stopped")

    def on_state_changed(self) -> None:
        self.publisher.mark_dirty()
