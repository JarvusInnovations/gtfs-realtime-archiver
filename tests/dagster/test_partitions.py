"""Tests for partition definitions."""

import dagster as dg

from dagster_pipeline.defs.partitions import (
    compaction_partitions,
    daily_partitions,
    feed_partitions,
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
    """Tests for feed partition definition."""

    def test_partition_type(self) -> None:
        """Test that feed partitions is a DynamicPartitionsDefinition."""
        assert isinstance(feed_partitions, dg.DynamicPartitionsDefinition)

    def test_partition_name(self) -> None:
        """Test that the partition name is 'feed'."""
        assert feed_partitions.name == "feed"


class TestCompactionPartitions:
    """Tests for multi-dimensional compaction partition definition."""

    def test_partition_type(self) -> None:
        """Test that compaction partitions is a MultiPartitionsDefinition."""
        assert isinstance(compaction_partitions, dg.MultiPartitionsDefinition)

    def test_has_two_dimensions(self) -> None:
        """Test that partitions have exactly two dimensions."""
        assert len(compaction_partitions.partitions_defs) == 2

    def test_dimension_names(self) -> None:
        """Test that the dimensions are named correctly."""
        dimension_names = {p.name for p in compaction_partitions.partitions_defs}
        assert dimension_names == {"date", "feed"}

    def test_multi_partition_key_construction(self) -> None:
        """Test that MultiPartitionKey can be constructed correctly."""
        key = dg.MultiPartitionKey({"date": "2026-01-03", "feed": "example.com/feed"})
        assert key.keys_by_dimension["date"] == "2026-01-03"
        assert key.keys_by_dimension["feed"] == "example.com/feed"
