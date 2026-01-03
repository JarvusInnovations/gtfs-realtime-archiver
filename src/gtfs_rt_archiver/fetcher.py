"""HTTP fetcher for GTFS-RT feeds with retry logic."""

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from gtfs_rt_archiver.models import AuthType, FeedConfig


class NonRetryableError(Exception):
    """Error that should not be retried (e.g., 4xx client errors)."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


@dataclass
class FetchResult:
    """Result of a successful feed fetch."""

    content: bytes
    headers: dict[str, str]
    status_code: int
    fetch_timestamp: datetime
    duration_ms: float
    content_length: int

    @property
    def content_type(self) -> str | None:
        """Get the content-type header if present."""
        return self.headers.get("content-type")

    @property
    def etag(self) -> str | None:
        """Get the etag header if present."""
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        """Get the last-modified header if present."""
        return self.headers.get("last-modified")


# HTTP status codes that should not be retried
NON_RETRYABLE_STATUS_CODES = {
    400,  # Bad request (our fault)
    401,  # Unauthorized (config issue)
    403,  # Forbidden (config issue)
    404,  # Not found (URL changed)
    410,  # Gone (feed discontinued)
}

# Exception types that warrant a retry
RETRYABLE_EXCEPTIONS = (
    httpx.TransportError,  # Connection errors
    httpx.TimeoutException,  # Timeouts
    httpx.HTTPStatusError,  # 5xx server errors (after raise_for_status)
)


def create_retrying(feed: FeedConfig) -> AsyncRetrying:
    """Create a tenacity AsyncRetrying instance based on feed configuration.

    Args:
        feed: Feed configuration with retry settings.

    Returns:
        An AsyncRetrying instance for use in async for loops.
    """
    return AsyncRetrying(
        stop=stop_after_attempt(feed.retry.max_attempts),
        wait=wait_exponential(
            multiplier=feed.retry.backoff_base,
            max=feed.retry.backoff_max,
        ),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )


async def _do_fetch(
    client: httpx.AsyncClient,
    feed: FeedConfig,
) -> FetchResult:
    """Perform the actual HTTP fetch (single attempt).

    Args:
        client: Async HTTP client to use for the request.
        feed: Feed configuration.

    Returns:
        FetchResult containing the feed content and metadata.

    Raises:
        NonRetryableError: For 4xx client errors that should not be retried.
        httpx.HTTPStatusError: For 5xx server errors.
        httpx.TransportError: For network errors.
        httpx.TimeoutException: For timeout errors.
    """
    fetch_start = datetime.now(UTC)

    # Parse URL to extract existing query parameters
    parsed_url = urlparse(str(feed.url))
    existing_params: dict[str, str] = {}

    # Convert parse_qs result (dict[str, list[str]]) to dict[str, str]
    if parsed_url.query:
        qs_dict = parse_qs(parsed_url.query)
        existing_params = {k: v[0] for k, v in qs_dict.items()}

    # Build headers and params from auth config
    headers: dict[str, str] | None = None
    params: dict[str, str] = existing_params.copy()  # Start with existing params

    if feed.auth is not None and feed.auth.resolved_value is not None:
        if feed.auth.type == AuthType.HEADER:
            headers = {feed.auth.key: feed.auth.resolved_value}
        elif feed.auth.type == AuthType.QUERY:
            # Merge auth param with existing params
            params[feed.auth.key] = feed.auth.resolved_value

    # Build clean URL without query string (httpx will rebuild it from params)
    clean_url = urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        parsed_url.params,
        '',  # Empty query string - will be rebuilt from params dict
        parsed_url.fragment,
    ))

    response = await client.get(
        clean_url,
        params=params if params else None,
        headers=headers,
        timeout=feed.timeout_seconds,
    )

    duration_ms = (datetime.now(UTC) - fetch_start).total_seconds() * 1000

    # Check for non-retryable client errors
    if response.status_code in NON_RETRYABLE_STATUS_CODES:
        raise NonRetryableError(
            response.status_code,
            f"Non-retryable error for feed {feed.id}",
        )

    # Raise for other error status codes (5xx will be retried)
    response.raise_for_status()

    return FetchResult(
        content=response.content,
        headers=dict(response.headers),
        status_code=response.status_code,
        fetch_timestamp=fetch_start,
        duration_ms=duration_ms,
        content_length=len(response.content),
    )


async def fetch_feed(
    client: httpx.AsyncClient,
    feed: FeedConfig,
) -> FetchResult:
    """Fetch a single GTFS-RT feed with retry logic.

    Args:
        client: Async HTTP client to use for the request.
        feed: Feed configuration.

    Returns:
        FetchResult containing the feed content and metadata.

    Raises:
        NonRetryableError: For 4xx client errors that should not be retried.
        httpx.HTTPStatusError: For 5xx server errors (after retry exhaustion).
        httpx.TransportError: For network errors (after retry exhaustion).
        httpx.TimeoutException: For timeout errors (after retry exhaustion).
    """
    retrying = create_retrying(feed)

    async for attempt in retrying:
        with attempt:
            return await _do_fetch(client, feed)

    # This should never be reached due to reraise=True
    raise RuntimeError("Retry loop exited without returning or raising")


async def fetch_feed_safe(
    client: httpx.AsyncClient,
    feed: FeedConfig,
) -> FetchResult | None:
    """Fetch a feed, returning None on failure instead of raising.

    This is a convenience wrapper that catches all exceptions and returns None,
    useful for scenarios where you want to continue processing other feeds
    even if one fails.

    Args:
        client: Async HTTP client to use for the request.
        feed: Feed configuration.

    Returns:
        FetchResult on success, None on any error.
    """
    try:
        return await fetch_feed(client, feed)
    except (NonRetryableError, httpx.HTTPError, RetryError):
        return None


def create_http_client(max_connections: int = 100) -> httpx.AsyncClient:
    """Create an async HTTP client with connection pooling.

    Args:
        max_connections: Maximum number of concurrent connections.

    Returns:
        Configured httpx.AsyncClient instance.
    """
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_connections // 2,
    )

    return httpx.AsyncClient(
        limits=limits,
        follow_redirects=True,
    )
