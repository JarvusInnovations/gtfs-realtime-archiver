"""Tests for GCS storage writer module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gtfs_rt_archiver.fetcher import FetchResult
from gtfs_rt_archiver.models import FeedConfig
from gtfs_rt_archiver.storage import (
    StorageWriter,
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
        agency="test-agency",
    )


@pytest.fixture
def feed_config_no_agency() -> FeedConfig:
    """Create a feed configuration without agency."""
    return FeedConfig(
        id="orphan-feed",
        name="Orphan Feed",
        url="https://example.com/orphan.pb",
        feed_type="trip_updates",
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


class TestGenerateStoragePath:
    """Tests for generate_storage_path function."""

    def test_basic_path(self, feed_config: FeedConfig) -> None:
        """Test basic path generation."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp)

        assert path == (
            "vehicle_positions/agency=test-agency/dt=2025-01-15/hour=14/"
            "test-feed/2025-01-15T14:20:30.123Z.pb"
        )

    def test_path_with_prefix(self, feed_config: FeedConfig) -> None:
        """Test path generation with prefix."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp, prefix="raw/gtfs-rt")

        assert path.startswith("raw/gtfs-rt/vehicle_positions/")

    def test_path_with_prefix_trailing_slash(self, feed_config: FeedConfig) -> None:
        """Test that trailing slashes are handled."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp, prefix="raw/")

        assert path.startswith("raw/vehicle_positions/")
        assert "//" not in path

    def test_path_without_agency(self, feed_config_no_agency: FeedConfig) -> None:
        """Test path generation when agency is None."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config_no_agency, timestamp)

        assert "agency=unknown" in path

    def test_different_extension(self, feed_config: FeedConfig) -> None:
        """Test path generation with different extension."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config, timestamp, extension="meta")

        assert path.endswith(".meta")

    def test_feed_type_in_path(self, feed_config_no_agency: FeedConfig) -> None:
        """Test that feed_type is correctly included."""
        timestamp = datetime(2025, 1, 15, 14, 20, 30, 123000, tzinfo=UTC)
        path = generate_storage_path(feed_config_no_agency, timestamp)

        assert path.startswith("trip_updates/")


class TestGenerateMetadata:
    """Tests for generate_metadata function."""

    def test_basic_metadata(
        self, feed_config: FeedConfig, fetch_result: FetchResult
    ) -> None:
        """Test basic metadata generation."""
        metadata = generate_metadata(feed_config, fetch_result)

        assert metadata["feed_id"] == "test-feed"
        assert metadata["url"] == "https://example.com/feed.pb"
        assert metadata["duration_ms"] == 245.5
        assert metadata["response_code"] == 200
        assert metadata["content_length"] == 16
        assert metadata["content_type"] == "application/x-protobuf"

    def test_headers_filtered(
        self, feed_config: FeedConfig, fetch_result: FetchResult
    ) -> None:
        """Test that only allowed headers are included."""
        metadata = generate_metadata(feed_config, fetch_result)
        headers = metadata["headers"]

        assert isinstance(headers, dict)
        assert "etag" in headers
        assert "last-modified" in headers
        assert "content-type" in headers
        # Custom headers should be filtered out
        assert "x-custom-header" not in headers

    def test_timestamp_format(
        self, feed_config: FeedConfig, fetch_result: FetchResult
    ) -> None:
        """Test that timestamp is in ISO format."""
        metadata = generate_metadata(feed_config, fetch_result)

        assert "2025-01-15" in str(metadata["fetch_timestamp"])


class TestStorageWriter:
    """Tests for StorageWriter class."""

    def test_initialization(self) -> None:
        """Test basic initialization."""
        writer = StorageWriter(bucket="test-bucket", prefix="archives/")

        assert writer.bucket == "test-bucket"
        assert writer.prefix == "archives/"
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
