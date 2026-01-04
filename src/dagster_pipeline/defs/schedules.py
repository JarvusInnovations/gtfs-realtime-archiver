"""Schedules for the compaction pipeline."""

from datetime import timedelta

import dagster as dg

from dagster_pipeline.defs.assets import (
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)
from dagster_pipeline.defs.partitions import daily_partitions

# Define the job for daily compaction
daily_compaction_job = dg.define_asset_job(
    name="daily_compaction_job",
    selection=[
        vehicle_positions_parquet,
        trip_updates_parquet,
        service_alerts_parquet,
    ],
    partitions_def=daily_partitions,
)


@dg.schedule(
    job=daily_compaction_job,
    cron_schedule="0 2 * * *",  # 2am UTC daily
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def compaction_schedule(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    """Daily compaction schedule - runs at 2am UTC to process yesterday's data.

    This gives a 2-hour buffer after UTC midnight for any late-arriving data.
    """
    # Get yesterday's date as the partition key
    scheduled_date = context.scheduled_execution_time
    partition_date = (scheduled_date - timedelta(days=1)).strftime("%Y-%m-%d")

    return dg.RunRequest(
        run_key=f"compaction_{partition_date}",
        partition_key=partition_date,
    )
