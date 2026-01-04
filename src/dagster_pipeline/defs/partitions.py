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
