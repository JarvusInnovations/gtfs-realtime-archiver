"""Tests for health check and metrics HTTP server."""

import time
from unittest.mock import MagicMock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from gtfs_rt_archiver.health import HealthServer
from gtfs_rt_archiver.metrics import _last_success_timestamps
from gtfs_rt_archiver.models import FeedConfig, FeedType


class TestHealthServerEndpoints(AioHTTPTestCase):
    """Test health server HTTP endpoints using aiohttp test client."""

    async def get_application(self) -> web.Application:
        """Create application for testing."""
        self.health_server = HealthServer(port=8080, scheduler=None)
        app = web.Application()
        app.router.add_get("/health", self.health_server._handle_health)
        app.router.add_get("/health/feeds", self.health_server._handle_feeds)
        app.router.add_get("/ready", self.health_server._handle_ready)
        app.router.add_get("/metrics", self.health_server._handle_metrics)
        return app

    @unittest_run_loop
    async def test_health_endpoint_returns_healthy(self) -> None:
        """Test /health returns healthy status."""
        resp = await self.client.get("/health")
        assert resp.status == 200

        data = await resp.json()
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data

    @unittest_run_loop
    async def test_health_endpoint_no_scheduler(self) -> None:
        """Test /health without scheduler doesn't include scheduler info."""
        resp = await self.client.get("/health")
        data = await resp.json()

        assert "scheduler" not in data
        assert "feeds" not in data

    @unittest_run_loop
    async def test_ready_endpoint_without_scheduler(self) -> None:
        """Test /ready returns ready when no scheduler configured."""
        resp = await self.client.get("/ready")
        assert resp.status == 200

        data = await resp.json()
        assert data["status"] == "ready"

    @unittest_run_loop
    async def test_feeds_endpoint_without_scheduler(self) -> None:
        """Test /health/feeds returns 503 when no scheduler configured."""
        resp = await self.client.get("/health/feeds")
        assert resp.status == 503

        data = await resp.json()
        assert data["error"] == "no scheduler"

    @unittest_run_loop
    async def test_metrics_endpoint_returns_prometheus_format(self) -> None:
        """Test /metrics returns OpenMetrics format."""
        resp = await self.client.get("/metrics")
        assert resp.status == 200
        assert "openmetrics" in resp.content_type or "text/plain" in resp.content_type

        text = await resp.text()
        # Should contain OpenMetrics format metadata
        assert "# HELP" in text or "# TYPE" in text or len(text) > 0
        # OpenMetrics format includes UNIT declarations for time metrics
        assert "# UNIT" in text or "# TYPE" in text


def _make_feed(feed_id: str, agency_id: str, feed_type: FeedType) -> FeedConfig:
    """Create a FeedConfig for testing."""
    return FeedConfig(
        id=feed_id,
        name=f"Test {feed_id}",
        url=f"https://example.com/{feed_id}",  # type: ignore[arg-type]
        feed_type=feed_type,
        agency_id=agency_id,
        agency_name=f"Test Agency {agency_id}",
    )


class TestHealthServerWithScheduler(AioHTTPTestCase):
    """Test health server with a mocked scheduler."""

    async def get_application(self) -> web.Application:
        """Create application with mocked scheduler."""
        self.test_feeds = [
            _make_feed("feed-1", "agency-a", FeedType.VEHICLE_POSITIONS),
            _make_feed("feed-2", "agency-a", FeedType.TRIP_UPDATES),
            _make_feed("feed-3", "agency-b", FeedType.SERVICE_ALERTS),
        ]

        self.mock_scheduler = MagicMock()
        self.mock_scheduler.is_running = True
        self.mock_scheduler.get_job_count.return_value = len(self.test_feeds)
        self.mock_scheduler.active_feeds = self.test_feeds

        self.health_server = HealthServer(port=8080, scheduler=self.mock_scheduler)
        app = web.Application()
        app.router.add_get("/health", self.health_server._handle_health)
        app.router.add_get("/health/feeds", self.health_server._handle_feeds)
        app.router.add_get("/ready", self.health_server._handle_ready)
        app.router.add_get("/metrics", self.health_server._handle_metrics)
        return app

    @unittest_run_loop
    async def test_health_includes_scheduler_info(self) -> None:
        """Test /health includes scheduler information."""
        resp = await self.client.get("/health")
        data = await resp.json()

        assert "scheduler" in data
        assert data["scheduler"]["running"] is True
        assert data["scheduler"]["jobs_scheduled"] == 3

        assert "feeds" in data
        assert data["feeds"]["total"] == 3

    @unittest_run_loop
    async def test_ready_when_scheduler_running(self) -> None:
        """Test /ready returns ready when scheduler is running."""
        resp = await self.client.get("/ready")
        assert resp.status == 200

        data = await resp.json()
        assert data["status"] == "ready"

    @unittest_run_loop
    async def test_feeds_endpoint_returns_per_feed_status(self) -> None:
        """Test /health/feeds returns per-feed list with expected fields."""
        resp = await self.client.get("/health/feeds")
        assert resp.status == 200

        data = await resp.json()
        assert len(data) == 3

        first = data[0]
        assert first["feed_id"] == "feed-1"
        assert first["agency_id"] == "agency-a"
        assert first["feed_type"] == "vehicle_positions"
        assert first["interval_seconds"] == 20
        assert first["last_success_seconds_ago"] is None

    @unittest_run_loop
    async def test_feeds_endpoint_shows_last_success(self) -> None:
        """Test /health/feeds includes seconds since last success."""
        # Simulate a successful fetch 10 seconds ago
        _last_success_timestamps["feed-2"] = time.time() - 10.0

        try:
            resp = await self.client.get("/health/feeds")
            data = await resp.json()

            feed_2 = next(f for f in data if f["feed_id"] == "feed-2")
            assert feed_2["last_success_seconds_ago"] is not None
            assert 9.0 <= feed_2["last_success_seconds_ago"] <= 12.0

            # Other feeds should still be None
            feed_1 = next(f for f in data if f["feed_id"] == "feed-1")
            assert feed_1["last_success_seconds_ago"] is None
        finally:
            _last_success_timestamps.pop("feed-2", None)

    @unittest_run_loop
    async def test_ready_not_ready_when_scheduler_stopped(self) -> None:
        """Test /ready returns 503 when scheduler is not running."""
        self.mock_scheduler.is_running = False

        resp = await self.client.get("/ready")
        assert resp.status == 503

        data = await resp.json()
        assert data["status"] == "not_ready"
        assert data["reason"] == "scheduler_not_running"


class TestHealthServerLifecycle:
    """Tests for HealthServer start/stop lifecycle."""

    def test_initialization(self) -> None:
        """Test health server initialization."""
        server = HealthServer(port=9090)
        assert server.port == 9090
        assert server.scheduler is None
        assert server._app is None
        assert server._runner is None

    def test_initialization_with_scheduler(self) -> None:
        """Test health server initialization with scheduler."""
        mock_scheduler = MagicMock()
        server = HealthServer(port=8080, scheduler=mock_scheduler)
        assert server.scheduler is mock_scheduler

    async def test_start_and_stop(self) -> None:
        """Test starting and stopping the health server."""
        server = HealthServer(port=0)  # Port 0 lets OS assign a free port

        await server.start()
        assert server._app is not None
        assert server._runner is not None
        assert server._site is not None

        await server.stop()
        assert server._app is None
        assert server._runner is None
        assert server._site is None

    async def test_stop_without_start(self) -> None:
        """Test that stop is safe to call without start."""
        server = HealthServer(port=8080)
        # Should not raise
        await server.stop()


class TestGetHealthStatus:
    """Tests for _get_health_status method."""

    def test_basic_status_without_scheduler(self) -> None:
        """Test health status without scheduler."""
        server = HealthServer(port=8080)
        status = server._get_health_status()

        assert status["status"] == "healthy"
        assert "uptime_seconds" in status
        assert isinstance(status["uptime_seconds"], float)

    def test_status_with_running_scheduler(self) -> None:
        """Test health status with running scheduler."""
        mock_scheduler = MagicMock()
        mock_scheduler.is_running = True
        mock_scheduler.get_job_count.return_value = 10
        mock_scheduler.active_feeds = [MagicMock() for _ in range(10)]

        server = HealthServer(port=8080, scheduler=mock_scheduler)
        status = server._get_health_status()

        assert status["status"] == "healthy"
        assert status["scheduler"]["running"] is True
        assert status["scheduler"]["jobs_scheduled"] == 10
        assert status["feeds"]["total"] == 10

    def test_status_with_stopped_scheduler(self) -> None:
        """Test health status with stopped scheduler."""
        mock_scheduler = MagicMock()
        mock_scheduler.is_running = False
        mock_scheduler.get_job_count.return_value = 0
        mock_scheduler.active_feeds = []

        server = HealthServer(port=8080, scheduler=mock_scheduler)
        status = server._get_health_status()

        assert status["scheduler"]["running"] is False
        assert status["scheduler"]["jobs_scheduled"] == 0
        assert status["feeds"]["total"] == 0
