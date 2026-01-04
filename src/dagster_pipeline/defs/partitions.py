"""Partition definitions for the compaction pipeline."""

import dagster as dg

# Daily partitions using UTC midnight boundaries.
# The archiver stores data partitioned by UTC date, so we use the same boundaries.
# end_offset=1 excludes today (incomplete data) from available partitions.
daily_partitions = dg.DailyPartitionsDefinition(
    start_date="2026-01-01",
    end_offset=1,
    timezone="UTC",
)

# Dynamic partitions for feeds.
# Partition keys are stripped URLs (e.g., "gtfs.example.com/feed/rt").
# Feeds are discovered and added at runtime by the feed_discovery_sensor.
feed_partitions = dg.DynamicPartitionsDefinition(name="feed")

# Multi-dimensional partition combining date and feed.
# Each date|feed combination is a separate partition, enabling:
# - Per-feed failure isolation
# - Targeted backfills for specific feeds/dates
# - Feed-level monitoring in the Dagster UI
compaction_partitions = dg.MultiPartitionsDefinition(
    {
        "date": daily_partitions,
        "feed": feed_partitions,
    }
)
