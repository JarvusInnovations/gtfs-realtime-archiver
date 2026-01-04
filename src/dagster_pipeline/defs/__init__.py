"""Dagster definitions for the GTFS-RT compaction pipeline."""

import os

import dagster as dg
from dagster_pipeline.defs.assets import (
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)
from dagster_pipeline.defs.resources import GCSResource
from dagster_pipeline.defs.schedules import compaction_schedule

defs = dg.Definitions(
    assets=[
        vehicle_positions_parquet,
        trip_updates_parquet,
        service_alerts_parquet,
    ],
    schedules=[
        compaction_schedule,
    ],
    resources={
        "gcs": GCSResource(
            project_id=os.environ.get("GCP_PROJECT_ID"),
            protobuf_bucket=os.environ.get("GCS_BUCKET_RT_PROTOBUF", ""),
            parquet_bucket=os.environ.get("GCS_BUCKET_RT_PARQUET", ""),
        ),
    },
)
