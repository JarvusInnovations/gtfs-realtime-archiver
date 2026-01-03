"""Tests for HTTP fetcher module."""

from datetime import UTC, datetime

import httpx
import pytest
import respx
from httpx import Response

from gtfs_rt_archiver.fetcher import (
    FetchResult,
    NonRetryableError,
    create_http_client,
    fetch_feed,
    fetch_feed_safe,
)
from gtfs_rt_archiver.models import AuthConfig, AuthType, FeedConfig, RetryConfig


@pytest.fixture
def feed_config() -> FeedConfig:
    """Create a basic feed configuration for testing."""
    return FeedConfig(
        id="test-feed",
        name="Test Feed",
        url="https://example.com/feed.pb",
        feed_type="vehicle_positions",
        timeout_seconds=5,
        retry=RetryConfig(max_attempts=2, backoff_base=0.1, backoff_max=1.0),
    )


@pytest.fixture
def feed_config_with_header_auth() -> FeedConfig:
    """Create a feed configuration with header authentication."""
    config = FeedConfig(
        id="test-feed-auth",
        name="Test Feed with Auth",
        url="https://example.com/feed.pb",
        feed_type="vehicle_positions",
        auth=AuthConfig(
            type=AuthType.HEADER,
            secret_name="test-secret",
            key="Authorization",
            value="Bearer ${SECRET}",
        ),
    )
    # Simulate resolved secret
    config.auth.resolved_value = "Bearer test-token"
    return config


@pytest.fixture
def feed_config_with_query_auth() -> FeedConfig:
    """Create a feed configuration with query parameter authentication."""
    config = FeedConfig(
        id="test-feed-query-auth",
        name="Test Feed with Query Auth",
        url="https://example.com/feed.pb",
        feed_type="vehicle_positions",
        auth=AuthConfig(
            type=AuthType.QUERY,
            secret_name="test-secret",
            key="api_key",
            value="${SECRET}",
        ),
    )
    config.auth.resolved_value = "abc123"
    return config


class TestFetchResult:
    """Tests for FetchResult dataclass."""

    def test_content_type_property(self) -> None:
        """Test content_type property extracts header correctly."""
        result = FetchResult(
            content=b"test",
            headers={"content-type": "application/x-protobuf"},
            status_code=200,
            fetch_timestamp=datetime.now(UTC),
            duration_ms=100.0,
            content_length=4,
        )
        assert result.content_type == "application/x-protobuf"

    def test_content_type_missing(self) -> None:
        """Test content_type returns None when header missing."""
        result = FetchResult(
            content=b"test",
            headers={},
            status_code=200,
            fetch_timestamp=datetime.now(UTC),
            duration_ms=100.0,
            content_length=4,
        )
        assert result.content_type is None

    def test_etag_property(self) -> None:
        """Test etag property extracts header correctly."""
        result = FetchResult(
            content=b"test",
            headers={"etag": '"abc123"'},
            status_code=200,
            fetch_timestamp=datetime.now(UTC),
            duration_ms=100.0,
            content_length=4,
        )
        assert result.etag == '"abc123"'


class TestFetchFeed:
    """Tests for fetch_feed function."""

    @respx.mock
    async def test_successful_fetch(self, feed_config: FeedConfig) -> None:
        """Test successful feed fetch."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(
                200,
                content=b"protobuf-content",
                headers={"content-type": "application/x-protobuf"},
            )
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_feed(client, feed_config)

        assert result.content == b"protobuf-content"
        assert result.status_code == 200
        assert result.content_length == 16
        assert "content-type" in result.headers

    @respx.mock
    async def test_fetch_with_header_auth(
        self, feed_config_with_header_auth: FeedConfig
    ) -> None:
        """Test fetch includes auth header."""
        route = respx.get("https://example.com/feed.pb").mock(
            return_value=Response(200, content=b"content")
        )

        async with httpx.AsyncClient() as client:
            await fetch_feed(client, feed_config_with_header_auth)

        assert route.called
        request = route.calls[0].request
        assert request.headers.get("Authorization") == "Bearer test-token"

    @respx.mock
    async def test_fetch_with_query_auth(
        self, feed_config_with_query_auth: FeedConfig
    ) -> None:
        """Test fetch includes auth query parameter."""
        route = respx.get("https://example.com/feed.pb").mock(
            return_value=Response(200, content=b"content")
        )

        async with httpx.AsyncClient() as client:
            await fetch_feed(client, feed_config_with_query_auth)

        assert route.called
        request = route.calls[0].request
        assert "api_key=abc123" in str(request.url)

    @respx.mock
    async def test_non_retryable_400(self, feed_config: FeedConfig) -> None:
        """Test 400 error raises NonRetryableError."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(400, content=b"Bad Request")
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(NonRetryableError) as exc_info:
                await fetch_feed(client, feed_config)

        assert exc_info.value.status_code == 400

    @respx.mock
    async def test_non_retryable_401(self, feed_config: FeedConfig) -> None:
        """Test 401 error raises NonRetryableError."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(401, content=b"Unauthorized")
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(NonRetryableError) as exc_info:
                await fetch_feed(client, feed_config)

        assert exc_info.value.status_code == 401

    @respx.mock
    async def test_non_retryable_403(self, feed_config: FeedConfig) -> None:
        """Test 403 error raises NonRetryableError."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(403, content=b"Forbidden")
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(NonRetryableError) as exc_info:
                await fetch_feed(client, feed_config)

        assert exc_info.value.status_code == 403

    @respx.mock
    async def test_non_retryable_404(self, feed_config: FeedConfig) -> None:
        """Test 404 error raises NonRetryableError."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(404, content=b"Not Found")
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(NonRetryableError) as exc_info:
                await fetch_feed(client, feed_config)

        assert exc_info.value.status_code == 404

    @respx.mock
    async def test_non_retryable_410(self, feed_config: FeedConfig) -> None:
        """Test 410 error raises NonRetryableError."""
        respx.get("https://example.com/feed.pb").mock(return_value=Response(410, content=b"Gone"))

        async with httpx.AsyncClient() as client:
            with pytest.raises(NonRetryableError) as exc_info:
                await fetch_feed(client, feed_config)

        assert exc_info.value.status_code == 410

    @respx.mock
    async def test_retries_on_500(self, feed_config: FeedConfig) -> None:
        """Test that 500 errors are retried."""
        route = respx.get("https://example.com/feed.pb").mock(
            side_effect=[
                Response(500, content=b"Server Error"),
                Response(200, content=b"success"),
            ]
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_feed(client, feed_config)

        assert result.content == b"success"
        assert route.call_count == 2

    @respx.mock
    async def test_retries_exhausted_500(self, feed_config: FeedConfig) -> None:
        """Test that HTTPStatusError is raised after retry exhaustion."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(500, content=b"Server Error")
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await fetch_feed(client, feed_config)

        assert exc_info.value.response.status_code == 500

    @respx.mock
    async def test_retries_on_transport_error(self, feed_config: FeedConfig) -> None:
        """Test that transport errors are retried."""
        route = respx.get("https://example.com/feed.pb").mock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                Response(200, content=b"success"),
            ]
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_feed(client, feed_config)

        assert result.content == b"success"
        assert route.call_count == 2


class TestFetchFeedSafe:
    """Tests for fetch_feed_safe function."""

    @respx.mock
    async def test_returns_result_on_success(self, feed_config: FeedConfig) -> None:
        """Test that successful fetch returns FetchResult."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(200, content=b"content")
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_feed_safe(client, feed_config)

        assert result is not None
        assert result.content == b"content"

    @respx.mock
    async def test_returns_none_on_non_retryable_error(self, feed_config: FeedConfig) -> None:
        """Test that NonRetryableError returns None."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(404, content=b"Not Found")
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_feed_safe(client, feed_config)

        assert result is None

    @respx.mock
    async def test_returns_none_on_http_error(self, feed_config: FeedConfig) -> None:
        """Test that HTTP errors return None after retry exhaustion."""
        respx.get("https://example.com/feed.pb").mock(
            return_value=Response(500, content=b"Server Error")
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_feed_safe(client, feed_config)

        assert result is None


class TestCreateHttpClient:
    """Tests for create_http_client function."""

    def test_creates_client_with_defaults(self) -> None:
        """Test creating client with default settings."""
        client = create_http_client()
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)

    def test_creates_client_with_custom_connections(self) -> None:
        """Test creating client with custom max_connections."""
        client = create_http_client(max_connections=50)
        assert client is not None
        # Can't easily verify limits, but the call should succeed
