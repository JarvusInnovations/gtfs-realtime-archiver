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

# Per-type dynamic partitions for feeds.
# Each feed type has its own partition definition so feeds only appear
# on assets that have data for that type.
# Partition keys are stripped URLs (e.g., "gtfs.example.com/feed/rt" for HTTPS,
# "~legacy.example.com/feed" for HTTP).
vehicle_positions_feeds = dg.DynamicPartitionsDefinition(name="vehicle_positions_feeds")
trip_updates_feeds = dg.DynamicPartitionsDefinition(name="trip_updates_feeds")
service_alerts_feeds = dg.DynamicPartitionsDefinition(name="service_alerts_feeds")

# Per-type multi-dimensional partitions combining date and feed.
# Each date|feed combination is a separate partition, enabling:
# - Per-feed failure isolation
# - Targeted backfills for specific feeds/dates
# - Feed-level monitoring in the Dagster UI
vehicle_positions_partitions = dg.MultiPartitionsDefinition(
    {
        "date": daily_partitions,
        "feed": vehicle_positions_feeds,
    }
)

trip_updates_partitions = dg.MultiPartitionsDefinition(
    {
        "date": daily_partitions,
        "feed": trip_updates_feeds,
    }
)

service_alerts_partitions = dg.MultiPartitionsDefinition(
    {
        "date": daily_partitions,
        "feed": service_alerts_feeds,
    }
)
