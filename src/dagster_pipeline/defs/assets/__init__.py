"""Assets for the Dagster pipeline."""

from dagster_pipeline.defs.assets.compaction import (
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)

__all__ = [
    "vehicle_positions_parquet",
    "trip_updates_parquet",
    "service_alerts_parquet",
]
