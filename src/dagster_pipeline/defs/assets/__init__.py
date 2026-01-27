"""Assets for the Dagster pipeline."""

from dagster_pipeline.defs.assets.compaction import (
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)
from dagster_pipeline.defs.assets.feeds_metadata import feeds_metadata
from dagster_pipeline.defs.assets.inventory import bucket_inventory

__all__ = [
    "vehicle_positions_parquet",
    "trip_updates_parquet",
    "service_alerts_parquet",
    "feeds_metadata",
    "bucket_inventory",
]
