"""Tests for partition definitions."""

import dagster as dg

from dagster_pipeline.defs.partitions import (
    daily_partitions,
    service_alerts_feeds,
    service_alerts_partitions,
    trip_updates_feeds,
    trip_updates_partitions,
    vehicle_positions_feeds,
    vehicle_positions_partitions,
)


class TestDailyPartitions:
    """Tests for daily partition definition."""

    def test_partition_type(self) -> None:
        """Test that daily partitions is a DailyPartitionsDefinition."""
        assert isinstance(daily_partitions, dg.DailyPartitionsDefinition)

    def test_start_date(self) -> None:
        """Test that start date is configured correctly."""
        assert daily_partitions.start.date().isoformat() == "2026-01-01"

    def test_timezone(self) -> None:
        """Test that timezone is UTC."""
        assert daily_partitions.timezone == "UTC"


class TestFeedPartitions:
    """Tests for feed partition definitions."""

    def test_vehicle_positions_feeds_type(self) -> None:
        """Test that vehicle_positions_feeds is a DynamicPartitionsDefinition."""
        assert isinstance(vehicle_positions_feeds, dg.DynamicPartitionsDefinition)

    def test_trip_updates_feeds_type(self) -> None:
        """Test that trip_updates_feeds is a DynamicPartitionsDefinition."""
        assert isinstance(trip_updates_feeds, dg.DynamicPartitionsDefinition)

    def test_service_alerts_feeds_type(self) -> None:
        """Test that service_alerts_feeds is a DynamicPartitionsDefinition."""
        assert isinstance(service_alerts_feeds, dg.DynamicPartitionsDefinition)

    def test_partition_names(self) -> None:
        """Test that each partition has a unique name."""
        assert vehicle_positions_feeds.name == "vehicle_positions_feeds"
        assert trip_updates_feeds.name == "trip_updates_feeds"
        assert service_alerts_feeds.name == "service_alerts_feeds"


class TestCompactionPartitions:
    """Tests for multi-dimensional compaction partition definitions."""

    def test_vehicle_positions_partition_type(self) -> None:
        """Test that vehicle_positions_partitions is a MultiPartitionsDefinition."""
        assert isinstance(vehicle_positions_partitions, dg.MultiPartitionsDefinition)

    def test_trip_updates_partition_type(self) -> None:
        """Test that trip_updates_partitions is a MultiPartitionsDefinition."""
        assert isinstance(trip_updates_partitions, dg.MultiPartitionsDefinition)

    def test_service_alerts_partition_type(self) -> None:
        """Test that service_alerts_partitions is a MultiPartitionsDefinition."""
        assert isinstance(service_alerts_partitions, dg.MultiPartitionsDefinition)

    def test_has_two_dimensions(self) -> None:
        """Test that all partitions have exactly two dimensions."""
        for partitions in [
            vehicle_positions_partitions,
            trip_updates_partitions,
            service_alerts_partitions,
        ]:
            assert len(partitions.partitions_defs) == 2

    def test_dimension_names(self) -> None:
        """Test that all partitions have date and feed dimensions."""
        for partitions in [
            vehicle_positions_partitions,
            trip_updates_partitions,
            service_alerts_partitions,
        ]:
            dimension_names = {p.name for p in partitions.partitions_defs}
            assert dimension_names == {"date", "feed"}

    def test_multi_partition_key_construction(self) -> None:
        """Test that MultiPartitionKey can be constructed correctly."""
        key = dg.MultiPartitionKey({"date": "2026-01-03", "feed": "example.com/feed"})
        assert key.keys_by_dimension["date"] == "2026-01-03"
        assert key.keys_by_dimension["feed"] == "example.com/feed"
