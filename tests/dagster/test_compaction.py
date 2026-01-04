"""Tests for Dagster compaction functions."""

import pytest
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets.compaction import (
    decode_base64url,
    encode_base64url,
    extract_service_alerts,
    extract_trip_updates,
    extract_vehicle_positions,
    parse_protobuf,
    partition_key_to_url,
    url_to_partition_key,
)


class TestDecodeBase64url:
    """Tests for base64url decoding."""

    def test_decode_simple(self) -> None:
        """Test decoding a simple base64url string."""
        # "https://example.com" encoded
        encoded = "aHR0cHM6Ly9leGFtcGxlLmNvbQ"
        result = decode_base64url(encoded)
        assert result == "https://example.com"

    def test_decode_with_url_safe_chars(self) -> None:
        """Test decoding with URL-safe characters (- and _)."""
        # URL with special chars that differ between base64 and base64url
        encoded = "aHR0cHM6Ly9leGFtcGxlLmNvbS9wYXRoP3F1ZXJ5PWZvbw"
        result = decode_base64url(encoded)
        assert result == "https://example.com/path?query=foo"

    def test_decode_handles_missing_padding(self) -> None:
        """Test that missing padding is handled correctly."""
        # base64url often omits padding
        encoded = "YWJj"  # "abc" - no padding needed
        assert decode_base64url(encoded) == "abc"

        encoded = "YWI"  # "ab" - needs 2 padding chars
        assert decode_base64url(encoded) == "ab"

        encoded = "YQ"  # "a" - needs 2 padding chars
        assert decode_base64url(encoded) == "a"


class TestEncodeBase64url:
    """Tests for base64url encoding."""

    def test_encode_simple(self) -> None:
        """Test encoding a simple URL."""
        url = "https://example.com"
        result = encode_base64url(url)
        assert result == "aHR0cHM6Ly9leGFtcGxlLmNvbQ"

    def test_encode_with_path_and_query(self) -> None:
        """Test encoding a URL with path and query parameters."""
        url = "https://example.com/path?query=foo"
        result = encode_base64url(url)
        assert result == "aHR0cHM6Ly9leGFtcGxlLmNvbS9wYXRoP3F1ZXJ5PWZvbw"

    def test_roundtrip(self) -> None:
        """Test that encode/decode round-trips correctly."""
        original = "https://gtfs.example.com/realtime/vehicle-positions"
        encoded = encode_base64url(original)
        decoded = decode_base64url(encoded)
        assert decoded == original


class TestUrlToPartitionKey:
    """Tests for URL to partition key conversion."""

    def test_https_no_prefix(self) -> None:
        """Test HTTPS URLs get no prefix (clean)."""
        url = "https://example.com/feed"
        assert url_to_partition_key(url) == "example.com/feed"

    def test_http_gets_prefix(self) -> None:
        """Test HTTP URLs get ~ prefix."""
        url = "http://example.com/feed"
        assert url_to_partition_key(url) == "~example.com/feed"

    def test_no_scheme_unchanged(self) -> None:
        """Test URL without scheme is unchanged."""
        url = "example.com/feed"
        assert url_to_partition_key(url) == "example.com/feed"

    def test_preserves_path_and_query(self) -> None:
        """Test that path and query are preserved."""
        url = "https://gtfs.example.com/api/v1/feed?key=abc"
        assert url_to_partition_key(url) == "gtfs.example.com/api/v1/feed?key=abc"

        url_http = "http://gtfs.example.com/api/v1/feed?key=abc"
        assert url_to_partition_key(url_http) == "~gtfs.example.com/api/v1/feed?key=abc"


class TestPartitionKeyToUrl:
    """Tests for partition key to URL conversion."""

    def test_no_prefix_becomes_https(self) -> None:
        """Test key without prefix becomes HTTPS URL."""
        key = "example.com/feed"
        assert partition_key_to_url(key) == "https://example.com/feed"

    def test_tilde_prefix_becomes_http(self) -> None:
        """Test key with ~ prefix becomes HTTP URL."""
        key = "~example.com/feed"
        assert partition_key_to_url(key) == "http://example.com/feed"

    def test_roundtrip_https(self) -> None:
        """Test HTTPS URL roundtrips correctly."""
        original = "https://gtfs.example.com/realtime"
        key = url_to_partition_key(original)
        recovered = partition_key_to_url(key)
        assert recovered == original

    def test_roundtrip_http(self) -> None:
        """Test HTTP URL roundtrips correctly."""
        original = "http://legacy.example.com/feed"
        key = url_to_partition_key(original)
        recovered = partition_key_to_url(key)
        assert recovered == original

    def test_base64url_roundtrip(self) -> None:
        """Test full roundtrip through base64url encoding."""
        original_url = "https://gtfs.example.com/realtime"
        original_base64 = encode_base64url(original_url)

        # URL -> partition key -> URL -> base64url
        key = url_to_partition_key(original_url)
        recovered_url = partition_key_to_url(key)
        recovered_base64 = encode_base64url(recovered_url)

        assert recovered_base64 == original_base64


class TestParseProtobuf:
    """Tests for protobuf parsing."""

    def test_parse_valid_protobuf(
        self, sample_vehicle_position_feed: gtfs_realtime_pb2.FeedMessage
    ) -> None:
        """Test parsing valid protobuf content."""
        content = sample_vehicle_position_feed.SerializeToString()
        result = parse_protobuf(content)

        assert result.header.gtfs_realtime_version == "2.0"
        assert result.header.timestamp == 1704067200
        assert len(result.entity) == 2

    def test_parse_invalid_protobuf(self) -> None:
        """Test that invalid protobuf raises DecodeError."""
        with pytest.raises(DecodeError):
            parse_protobuf(b"not valid protobuf data")

    def test_parse_empty_content(self) -> None:
        """Test parsing empty content (valid but empty message)."""
        result = parse_protobuf(b"")
        assert result.header.gtfs_realtime_version == ""


class TestExtractVehiclePositions:
    """Tests for vehicle position extraction."""

    def test_extract_full_vehicle_position(
        self, sample_vehicle_position_feed: gtfs_realtime_pb2.FeedMessage
    ) -> None:
        """Test extracting vehicle positions with all fields."""
        records = list(
            extract_vehicle_positions(
                sample_vehicle_position_feed,
                "test/file.pb",
                "https://example.com/feed",
            )
        )

        assert len(records) == 2

        # Check first record (full data)
        r = records[0]
        assert r["source_file"] == "test/file.pb"
        assert r["feed_url"] == "https://example.com/feed"
        assert r["feed_timestamp"] == 1704067200
        assert r["entity_id"] == "vehicle-1"
        assert r["trip_id"] == "trip-123"
        assert r["route_id"] == "route-A"
        assert r["direction_id"] == 0
        assert r["vehicle_id"] == "bus-001"
        assert r["vehicle_label"] == "Bus 1"
        assert r["latitude"] == pytest.approx(39.9526)
        assert r["longitude"] == pytest.approx(-75.1652)
        assert r["bearing"] == pytest.approx(180.0)
        assert r["speed"] == pytest.approx(12.5)
        assert r["current_stop_sequence"] == 5
        assert r["stop_id"] == "stop-100"
        assert r["timestamp"] == 1704067200

    def test_extract_minimal_vehicle_position(
        self, sample_vehicle_position_feed: gtfs_realtime_pb2.FeedMessage
    ) -> None:
        """Test extracting vehicle position with minimal fields."""
        records = list(
            extract_vehicle_positions(
                sample_vehicle_position_feed,
                "test/file.pb",
                "https://example.com/feed",
            )
        )

        # Check second record (minimal data)
        r = records[1]
        assert r["entity_id"] == "vehicle-2"
        assert r["latitude"] == pytest.approx(40.0)
        assert r["longitude"] == pytest.approx(-75.0)
        # Optional fields should be None
        assert r["trip_id"] is None
        assert r["vehicle_id"] is None
        assert r["bearing"] is None
        assert r["speed"] is None

    def test_extract_empty_feed(self, empty_feed: gtfs_realtime_pb2.FeedMessage) -> None:
        """Test extracting from empty feed."""
        records = list(extract_vehicle_positions(empty_feed, "test.pb", "http://test"))
        assert records == []


class TestExtractTripUpdates:
    """Tests for trip update extraction."""

    def test_extract_with_stop_time_updates(
        self, sample_trip_update_feed: gtfs_realtime_pb2.FeedMessage
    ) -> None:
        """Test extracting trip updates with stop time updates (denormalized)."""
        records = list(
            extract_trip_updates(
                sample_trip_update_feed,
                "test/file.pb",
                "https://example.com/feed",
            )
        )

        # First entity has 2 stop time updates, second has none (1 base record)
        assert len(records) == 3

        # Check first stop time update
        r = records[0]
        assert r["entity_id"] == "trip-update-1"
        assert r["trip_id"] == "trip-456"
        assert r["route_id"] == "route-B"
        assert r["trip_delay"] == 120
        assert r["stop_sequence"] == 1
        assert r["stop_id"] == "stop-A"
        assert r["arrival_delay"] == 60
        assert r["arrival_time"] == 1704067260
        assert r["departure_delay"] == 90
        assert r["departure_time"] == 1704067290

        # Check second stop time update
        r = records[1]
        assert r["stop_sequence"] == 2
        assert r["stop_id"] == "stop-B"
        assert r["arrival_delay"] == 120
        # No departure for this stop
        assert r["departure_delay"] is None

    def test_extract_without_stop_time_updates(
        self, sample_trip_update_feed: gtfs_realtime_pb2.FeedMessage
    ) -> None:
        """Test extracting trip update without stop time updates."""
        records = list(
            extract_trip_updates(
                sample_trip_update_feed,
                "test/file.pb",
                "https://example.com/feed",
            )
        )

        # Third record is from entity without stop time updates
        r = records[2]
        assert r["entity_id"] == "trip-update-2"
        assert r["trip_id"] == "trip-789"
        assert r["stop_sequence"] is None
        assert r["stop_id"] is None
        assert r["arrival_delay"] is None

    def test_extract_empty_feed(self, empty_feed: gtfs_realtime_pb2.FeedMessage) -> None:
        """Test extracting from empty feed."""
        records = list(extract_trip_updates(empty_feed, "test.pb", "http://test"))
        assert records == []


class TestExtractServiceAlerts:
    """Tests for service alert extraction."""

    def test_extract_with_informed_entities(
        self, sample_service_alert_feed: gtfs_realtime_pb2.FeedMessage
    ) -> None:
        """Test extracting service alerts with informed entities (denormalized)."""
        records = list(
            extract_service_alerts(
                sample_service_alert_feed,
                "test/file.pb",
                "https://example.com/feed",
            )
        )

        # First entity has 2 informed entities, second has none (1 base record)
        assert len(records) == 3

        # Check first informed entity
        r = records[0]
        assert r["entity_id"] == "alert-1"
        assert r["cause"] == gtfs_realtime_pb2.Alert.CONSTRUCTION
        assert r["effect"] == gtfs_realtime_pb2.Alert.DETOUR
        assert r["header_text"] == "Construction on Main St"
        assert r["description_text"] == "Route detoured due to construction work"
        assert r["active_period_start"] == 1704067200
        assert r["active_period_end"] == 1704153600
        assert r["agency_id"] == "agency-1"
        assert r["route_id"] == "route-A"
        assert r["stop_id"] is None

        # Check second informed entity (stop-based)
        r = records[1]
        assert r["entity_id"] == "alert-1"  # Same alert
        assert r["agency_id"] is None
        assert r["stop_id"] == "stop-100"

    def test_extract_without_informed_entities(
        self, sample_service_alert_feed: gtfs_realtime_pb2.FeedMessage
    ) -> None:
        """Test extracting service alert without informed entities."""
        records = list(
            extract_service_alerts(
                sample_service_alert_feed,
                "test/file.pb",
                "https://example.com/feed",
            )
        )

        # Third record is from entity without informed entities
        r = records[2]
        assert r["entity_id"] == "alert-2"
        assert r["header_text"] == "General announcement"
        assert r["agency_id"] is None
        assert r["route_id"] is None
        assert r["stop_id"] is None

    def test_extract_empty_feed(self, empty_feed: gtfs_realtime_pb2.FeedMessage) -> None:
        """Test extracting from empty feed."""
        records = list(extract_service_alerts(empty_feed, "test.pb", "http://test"))
        assert records == []
