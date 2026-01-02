"""Tests for main module's create_fetch_job function."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import Response

from gtfs_rt_archiver.__main__ import create_fetch_job
from gtfs_rt_archiver.fetcher import FetchResult
from gtfs_rt_archiver.models import FeedConfig


@pytest.fixture
def feed_config() -> FeedConfig:
    """Create a basic feed configuration."""
    return FeedConfig(
        id="test-feed",
        name="Test Feed",
        url="https://example.com/feed.pb",
        feed_type="vehicle_positions",
        agency="test-agency",
    )


@pytest.fixture
def fetch_result() -> FetchResult:
    """Create a sample fetch result."""
    return FetchResult(
        content=b"protobuf-content",
        headers={"content-type": "application/x-protobuf"},
        status_code=200,
        fetch_timestamp=datetime.now(UTC),
        duration_ms=150.0,
        content_length=16,
    )


@pytest.fixture
def mock_storage_writer() -> AsyncMock:
    """Create a mock storage writer."""
    writer = AsyncMock()
    writer.write = AsyncMock(return_value="path/to/file.pb")
    return writer


class TestCreateFetchJob:
    """Tests for create_fetch_job function."""

    @respx.mock
    async def test_successful_fetch_and_upload(
        self,
        feed_config: FeedConfig,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test successful fetch and upload flow."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(
                200,
                content=b"protobuf-content",
                headers={"content-type": "application/x-protobuf"},
            )
        )

        async with httpx.AsyncClient() as client:
            semaphore = asyncio.Semaphore(10)
            fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)

            await fetch_job(feed_config)

        # Verify storage was called
        mock_storage_writer.write.assert_called_once()
        call_args = mock_storage_writer.write.call_args
        assert call_args[0][0] == feed_config  # First arg is feed

    @respx.mock
    async def test_fetch_records_metrics(
        self,
        feed_config: FeedConfig,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test that fetch records metrics."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(200, content=b"content")
        )

        with (
            patch("gtfs_rt_archiver.__main__.record_fetch_attempt") as mock_attempt,
            patch("gtfs_rt_archiver.__main__.record_fetch_success") as mock_success,
            patch("gtfs_rt_archiver.__main__.record_upload_success") as mock_upload,
        ):
            async with httpx.AsyncClient() as client:
                semaphore = asyncio.Semaphore(10)
                fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)
                await fetch_job(feed_config)

            mock_attempt.assert_called_once_with("test-feed", "vehicle_positions", "test-agency")
            mock_success.assert_called_once()
            mock_upload.assert_called_once()

    @respx.mock
    async def test_non_retryable_error_handled(
        self,
        feed_config: FeedConfig,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test that 4xx errors are handled as non-retryable."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(404, content=b"Not Found")
        )

        with patch("gtfs_rt_archiver.__main__.record_fetch_error") as mock_error:
            async with httpx.AsyncClient() as client:
                semaphore = asyncio.Semaphore(10)
                fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)

                # Should not raise
                await fetch_job(feed_config)

            mock_error.assert_called_once()
            args = mock_error.call_args[0]
            assert args[3] == "http_404"  # error_type

        # Storage should not be called on fetch error
        mock_storage_writer.write.assert_not_called()

    @respx.mock
    async def test_timeout_error_handled(
        self,
        feed_config: FeedConfig,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test that timeout errors are handled."""
        respx.get("https://example.com/feed.pb").mock(side_effect=httpx.TimeoutException("Timeout"))

        with patch("gtfs_rt_archiver.__main__.record_fetch_error") as mock_error:
            async with httpx.AsyncClient() as client:
                semaphore = asyncio.Semaphore(10)
                fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)

                # Should not raise
                await fetch_job(feed_config)

            mock_error.assert_called_once()
            args = mock_error.call_args[0]
            assert args[3] == "timeout"

    @respx.mock
    async def test_transport_error_handled(
        self,
        feed_config: FeedConfig,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test that transport errors are handled."""
        respx.get("https://example.com/feed.pb").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch("gtfs_rt_archiver.__main__.record_fetch_error") as mock_error:
            async with httpx.AsyncClient() as client:
                semaphore = asyncio.Semaphore(10)
                fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)

                # Should not raise
                await fetch_job(feed_config)

            mock_error.assert_called_once()
            args = mock_error.call_args[0]
            assert args[3] == "transport"

    @respx.mock
    async def test_http_status_error_handled(
        self,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test that 5xx errors after retry exhaustion are handled."""
        # Configure feed with minimal retry to speed up test
        feed = FeedConfig(
            id="test-feed",
            name="Test Feed",
            url="https://example.com/feed.pb",
            feed_type="vehicle_positions",
            agency="test-agency",
            retry={"max_attempts": 1, "backoff_base": 0.1, "backoff_max": 1.0},
        )

        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(500, content=b"Server Error")
        )

        with patch("gtfs_rt_archiver.__main__.record_fetch_error") as mock_error:
            async with httpx.AsyncClient() as client:
                semaphore = asyncio.Semaphore(10)
                fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)

                # Should not raise
                await fetch_job(feed)

            mock_error.assert_called_once()
            args = mock_error.call_args[0]
            assert args[3] == "http_500"

    @respx.mock
    async def test_upload_error_handled(
        self,
        feed_config: FeedConfig,
    ) -> None:
        """Test that upload errors are handled and don't crash."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(200, content=b"content")
        )

        mock_storage = AsyncMock()
        mock_storage.write = AsyncMock(side_effect=Exception("Upload failed"))

        with patch("gtfs_rt_archiver.__main__.record_upload_error") as mock_error:
            async with httpx.AsyncClient() as client:
                semaphore = asyncio.Semaphore(10)
                fetch_job = await create_fetch_job(client, mock_storage, semaphore)

                # Should not raise
                await fetch_job(feed_config)

            mock_error.assert_called_once()
            args = mock_error.call_args[0]
            assert args[0] == "test-feed"
            assert args[3] == "Exception"  # error_type

    @respx.mock
    async def test_semaphore_limits_concurrency(
        self,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test that semaphore limits concurrent operations."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(200, content=b"content")
        )

        async with httpx.AsyncClient() as client:
            # Semaphore with limit of 2
            semaphore = asyncio.Semaphore(2)
            fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)

            # Track concurrent executions
            max_concurrent = 0
            current_concurrent = 0

            async def tracking_write(*_args: object, **_kwargs: object) -> str:
                nonlocal max_concurrent, current_concurrent
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
                await asyncio.sleep(0.01)  # Simulate some work
                current_concurrent -= 1
                return "path/to/file.pb"

            mock_storage_writer.write = tracking_write

            # Run 5 fetches concurrently
            feeds = [
                FeedConfig(
                    id=f"feed-{i}",
                    name=f"Feed {i}",
                    url="https://example.com/feed.pb",
                    feed_type="vehicle_positions",
                )
                for i in range(5)
            ]

            await asyncio.gather(*[fetch_job(f) for f in feeds])

            # Max concurrent should not exceed semaphore limit
            assert max_concurrent <= 2

    @respx.mock
    async def test_unknown_error_handled(
        self,
        feed_config: FeedConfig,
        mock_storage_writer: AsyncMock,
    ) -> None:
        """Test that unknown errors are caught and logged."""
        respx.get("https://example.com/feed.pb").mock(side_effect=RuntimeError("Unexpected error"))

        with patch("gtfs_rt_archiver.__main__.record_fetch_error") as mock_error:
            async with httpx.AsyncClient() as client:
                semaphore = asyncio.Semaphore(10)
                fetch_job = await create_fetch_job(client, mock_storage_writer, semaphore)

                # Should not raise
                await fetch_job(feed_config)

            mock_error.assert_called_once()
            args = mock_error.call_args[0]
            assert args[3] == "unknown"
