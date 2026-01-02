"""Health check and metrics HTTP server."""

import json
import time
from typing import TYPE_CHECKING

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

if TYPE_CHECKING:
    from gtfs_rt_archiver.scheduler import FeedScheduler


class HealthServer:
    """HTTP server for health checks and Prometheus metrics."""

    def __init__(
        self,
        port: int = 8080,
        scheduler: "FeedScheduler | None" = None,
    ) -> None:
        """Initialize the health server.

        Args:
            port: Port to listen on.
            scheduler: Optional scheduler for health status reporting.
        """
        self.port = port
        self.scheduler = scheduler
        self._start_time = time.time()
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def _get_health_status(self) -> dict[str, object]:
        """Get current health status.

        Returns:
            Dictionary containing health status information.
        """
        uptime = time.time() - self._start_time

        status: dict[str, object] = {
            "status": "healthy",
            "uptime_seconds": round(uptime, 2),
        }

        if self.scheduler is not None:
            status["scheduler"] = {
                "running": self.scheduler.is_running,
                "jobs_scheduled": self.scheduler.get_job_count(),
            }
            status["feeds"] = {
                "total": len(self.scheduler.active_feeds),
            }

        return status

    async def _handle_health(self, _request: web.Request) -> web.Response:
        """Handle /health endpoint.

        Args:
            request: HTTP request.

        Returns:
            JSON response with health status.
        """
        status = self._get_health_status()
        return web.json_response(status)

    async def _handle_ready(self, _request: web.Request) -> web.Response:
        """Handle /ready endpoint for Kubernetes readiness probes.

        Args:
            request: HTTP request.

        Returns:
            200 OK if ready, 503 if not.
        """
        if self.scheduler is not None and not self.scheduler.is_running:
            return web.Response(
                text=json.dumps({"status": "not_ready", "reason": "scheduler_not_running"}),
                status=503,
                content_type="application/json",
            )

        return web.json_response({"status": "ready"})

    async def _handle_metrics(self, _request: web.Request) -> web.Response:
        """Handle /metrics endpoint for Prometheus scraping.

        Args:
            request: HTTP request.

        Returns:
            Prometheus metrics in text format.
        """
        metrics = generate_latest()
        # Strip charset from content type since aiohttp adds it automatically
        content_type = CONTENT_TYPE_LATEST.split(";")[0]
        return web.Response(
            body=metrics,
            content_type=content_type,
            charset="utf-8",
        )

    async def start(self) -> None:
        """Start the HTTP server."""
        self._app = web.Application()
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/ready", self._handle_ready)
        self._app.router.add_get("/metrics", self._handle_metrics)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._app = None
            self._site = None
