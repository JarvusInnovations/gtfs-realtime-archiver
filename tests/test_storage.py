"""Tests for GCS storage writer module."""

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gtfs_rt_archiver.fetcher import FetchResult
from gtfs_rt_archiver.models import FeedConfig
from gtfs_rt_archiver.storage import (
    StorageWriter,
    encode_url_to_base64url,
    generate_metadata,
    generate_storage_path,
)


@pytest.fixture
def feed_config() -> FeedConfig:
    """Create a basic feed configuration for testing."""
    return FeedConfig(
        id="test-feed",
        name="Test Feed",
        url="https://example.com/feed.pb",
        feed_type="vehicle_positions",
    )


@pytest.fixture
def feed_config_with_params() -> FeedConfig:
    """Create a feed configuration with query parameters."""
    return FeedConfig(
        id="test-feed-params",
        name="Test Feed with Params",
        url="https://api.example.com/feed",
        feed_type="trip_updates",
        query_params={"key": "abc123", "format": "pb"},
    )


@pytest.fixture
def fetch_result() -> FetchResult:
    """Create a sample fetch result."""
    return FetchResult(
        content=b"protobuf-content",
        headers={
            "content-type": "application/x-protobuf",
            "etag": '"abc123"',
            "last-modified": "Wed, 15 Jan 2025 14:19:58 GMT",
            "x-custom-header": "should-be-filtered",
        },
        status_code=200,
        fetch_timestamp=datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC),
        duration_ms=245.5,
        content_length=16,
    )


def _decode_base64url(encoded: str) -> str:
    """Decode base64url string (add padding back for decoding)."""
    padded = encoded + "=" * (4 - len(encoded) % 4) if len(encoded) % 4 else encoded
    return base64.urlsafe_b64decode(padded).decode("utf-8")


class TestEncodeUrlToBase64url:
    """Tests for encode_url_to_base64url function."""

    def test_simple_url(self) -> None:
        """Test encoding a simple URL."""
        url = "https://example.com/feed.pb"
        result = encode_url_to_base64url(url)

        # Verify it's valid base64url (no +, /, or = characters)
        assert "+" not in result
        assert "/" not in result
        assert "=" not in result

        # Verify round-trip
        decoded = _decode_base64url(result)
        assert decoded == url

    def test_url_with_query_params(self) -> None:
        """Test encoding URL with query parameters."""
        url = "https://api.example.com/feed"
        query_params = {"key": "abc123", "format": "pb"}
        result = encode_url_to_base64url(url, query_params)

        # Verify round-trip includes query params
        decoded = _decode_base64url(result)
        assert "key=abc123" in decoded
        assert "format=pb" in decoded

    def test_empty_query_params(self) -> None:
        """Test that empty query params don't affect URL."""
        url = "https://example.com/feed.pb"
        result_none = encode_url_to_base64url(url, None)
        result_empty = encode_url_to_base64url(url, {})
        result_plain = encode_url_to_base64url(url)

        assert result_none == result_plain
        assert result_empty == result_plain


class TestGenerateStoragePath:
    """Tests for generate_storage_path function."""

    def test_basic_path(self, feed_config: FeedConfig) -> None:
        """Test basic path generation."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp)

        # Check structure
        parts = path.split("/")
        assert len(parts) == 5
        assert parts[0] == "vehicle_positions"
        assert parts[1] == "date=2025-01-15"
        assert parts[2] == "hour=2025-01-15T14:00:00Z"
        assert parts[3].startswith("base64url=")
        assert parts[4] == "2025-01-15T14:20:30.123Z.pb"

    def test_base64url_encoding(self, feed_config: FeedConfig) -> None:
        """Test that base64url partition is correctly encoded."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp)

        # Extract base64url value
        parts = path.split("/")
        base64url_part = parts[3]
        assert base64url_part.startswith("base64url=")
        encoded = base64url_part[len("base64url=") :]

        # Verify no padding characters
        assert "=" not in encoded

        # Decode and verify it matches the feed URL
        decoded = _decode_base64url(encoded)
        assert decoded == str(feed_config.url)

    def test_path_with_query_params(self, feed_config_with_params: FeedConfig) -> None:
        """Test path generation with query parameters."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config_with_params, timestamp)

        # Decode base64url and verify query params included
        parts = path.split("/")
        encoded = parts[3][len("base64url=") :]
        decoded = _decode_base64url(encoded)
        assert "key=abc123" in decoded
        assert "format=pb" in decoded

    def test_different_extension(self, feed_config: FeedConfig) -> None:
        """Test path generation with different extension."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp, extension="meta")

        assert path.endswith(".meta")

    def test_feed_type_in_path(self, feed_config_with_params: FeedConfig) -> None:
        """Test that feed_type is correctly included."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config_with_params, timestamp)

        assert path.startswith("trip_updates/")

    def test_hour_boundary_truncation(self, feed_config: FeedConfig) -> None:
        """Test that hour is truncated to boundary."""
        # Timestamp at 14:59:59 should still have hour=14:00:00
        timestamp = datetime(2025, 1, 15, 14, 59, 59, 999000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp)

        assert "hour=2025-01-15T14:00:00Z" in path


class TestGenerateMetadata:
    """Tests for generate_metadata function."""

    def test_basic_metadata(self, feed_config: FeedConfig, fetch_result: FetchResult) -> None:
        """Test basic metadata generation."""
        metadata = generate_metadata(feed_config, fetch_result)

        assert metadata["feed_id"] == "test-feed"
        assert metadata["url"] == "https://example.com/feed.pb"
        assert metadata["duration_ms"] == 245.5
        assert metadata["response_code"] == 200
        assert metadata["content_length"] == 16
        assert metadata["content_type"] == "application/x-protobuf"

    def test_headers_filtered(self, feed_config: FeedConfig, fetch_result: FetchResult) -> None:
        """Test that only allowed headers are included."""
        metadata = generate_metadata(feed_config, fetch_result)
        headers = metadata["headers"]

        assert isinstance(headers, dict)
        assert "etag" in headers
        assert "last-modified" in headers
        assert "content-type" in headers
        # Custom headers should be filtered out
        assert "x-custom-header" not in headers

    def test_timestamp_format(self, feed_config: FeedConfig, fetch_result: FetchResult) -> None:
        """Test that timestamp is in ISO format."""
        metadata = generate_metadata(feed_config, fetch_result)

        assert "2025-01-15" in str(metadata["fetch_timestamp"])


class TestStorageWriter:
    """Tests for StorageWriter class."""

    def test_initialization(self) -> None:
        """Test basic initialization."""
        writer = StorageWriter(bucket="test-bucket")

        assert writer.bucket == "test-bucket"
        assert writer.write_metadata is True

    def test_initialization_no_metadata(self) -> None:
        """Test initialization with metadata disabled."""
        writer = StorageWriter(bucket="test-bucket", write_metadata=False)

        assert writer.write_metadata is False

    @patch("gtfs_rt_archiver.storage.Storage")
    async def test_write_uploads_content(
        self,
        mock_storage_class: MagicMock,
        feed_config: FeedConfig,
        fetch_result: FetchResult,
    ) -> None:
        """Test that write uploads content to GCS."""
        mock_storage = AsyncMock()
        mock_storage_class.return_value = mock_storage

        writer = StorageWriter(bucket="test-bucket", write_metadata=False)
        path = await writer.write(feed_config, fetch_result)

        # Verify upload was called
        mock_storage.upload.assert_called_once()
        call_kwargs = mock_storage.upload.call_args.kwargs
        assert call_kwargs["bucket"] == "test-bucket"
        assert call_kwargs["file_data"] == b"protobuf-content"
        assert call_kwargs["content_type"] == "application/x-protobuf"
        assert path.endswith(".pb")

    @patch("gtfs_rt_archiver.storage.Storage")
    async def test_write_uploads_metadata(
        self,
        mock_storage_class: MagicMock,
        feed_config: FeedConfig,
        fetch_result: FetchResult,
    ) -> None:
        """Test that write uploads metadata when enabled."""
        mock_storage = AsyncMock()
        mock_storage_class.return_value = mock_storage

        writer = StorageWriter(bucket="test-bucket", write_metadata=True)
        await writer.write(feed_config, fetch_result)

        # Verify both content and metadata were uploaded
        assert mock_storage.upload.call_count == 2

        # Check the second call is for metadata
        calls = mock_storage.upload.call_args_list
        metadata_call = calls[1]
        assert metadata_call.kwargs["content_type"] == "application/json"
        assert metadata_call.kwargs["object_name"].endswith(".meta")

    @patch("gtfs_rt_archiver.storage.Storage")
    async def test_write_skips_metadata(
        self,
        mock_storage_class: MagicMock,
        feed_config: FeedConfig,
        fetch_result: FetchResult,
    ) -> None:
        """Test that metadata is skipped when disabled."""
        mock_storage = AsyncMock()
        mock_storage_class.return_value = mock_storage

        writer = StorageWriter(bucket="test-bucket", write_metadata=False)
        await writer.write(feed_config, fetch_result)

        # Verify only content was uploaded
        assert mock_storage.upload.call_count == 1

    @patch("gtfs_rt_archiver.storage.Storage")
    async def test_close(self, mock_storage_class: MagicMock) -> None:
        """Test that close releases resources."""
        mock_storage = AsyncMock()
        mock_storage_class.return_value = mock_storage

        writer = StorageWriter(bucket="test-bucket")

        # Force creation of storage client
        await writer._get_storage()

        # Close the writer
        await writer.close()

        mock_storage.close.assert_called_once()
        assert writer._storage is None

    async def test_close_without_init(self) -> None:
        """Test that close works when storage was never initialized."""
        writer = StorageWriter(bucket="test-bucket")

        # Should not raise
        await writer.close()

    @patch("gtfs_rt_archiver.storage.Storage")
    async def test_storage_reuse(
        self,
        mock_storage_class: MagicMock,
        feed_config: FeedConfig,
        fetch_result: FetchResult,
    ) -> None:
        """Test that storage client is reused across writes."""
        mock_storage = AsyncMock()
        mock_storage_class.return_value = mock_storage

        writer = StorageWriter(bucket="test-bucket", write_metadata=False)

        # Multiple writes
        await writer.write(feed_config, fetch_result)
        await writer.write(feed_config, fetch_result)

        # Storage should only be created once
        assert mock_storage_class.call_count == 1
