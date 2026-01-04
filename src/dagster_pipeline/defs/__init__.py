"""Dagster definitions for the GTFS-RT compaction pipeline."""

import os

import dagster as dg

from dagster_pipeline.defs.assets import (
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)
from dagster_pipeline.defs.resources import GCSResource
from dagster_pipeline.defs.schedules import (
    service_alerts_compaction_job,
    service_alerts_schedule,
    trip_updates_compaction_job,
    trip_updates_schedule,
    vehicle_positions_compaction_job,
    vehicle_positions_schedule,
)
from dagster_pipeline.defs.sensors import feed_discovery_sensor

defs = dg.Definitions(
    assets=[
        vehicle_positions_parquet,
        trip_updates_parquet,
        service_alerts_parquet,
    ],
    jobs=[
        vehicle_positions_compaction_job,
        trip_updates_compaction_job,
        service_alerts_compaction_job,
    ],
    schedules=[
        vehicle_positions_schedule,
        trip_updates_schedule,
        service_alerts_schedule,
    ],
    sensors=[
        feed_discovery_sensor,
    ],
    resources={
        "gcs": GCSResource(
            project_id=os.environ.get("GCP_PROJECT_ID"),
            protobuf_bucket=os.environ.get("GCS_BUCKET_RT_PROTOBUF", ""),
            parquet_bucket=os.environ.get("GCS_BUCKET_RT_PARQUET", ""),
        ),
    },
)
