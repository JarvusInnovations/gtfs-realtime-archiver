"""Schedules for the compaction pipeline."""

from datetime import timedelta

import dagster as dg

from dagster_pipeline.defs.assets import (
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)
from dagster_pipeline.defs.partitions import compaction_partitions

# Define the job for daily compaction
daily_compaction_job = dg.define_asset_job(
    name="daily_compaction_job",
    selection=[
        vehicle_positions_parquet,
        trip_updates_parquet,
        service_alerts_parquet,
    ],
    partitions_def=compaction_partitions,
)


@dg.schedule(
    job=daily_compaction_job,
    cron_schedule="0 2 * * *",  # 2am UTC daily
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def compaction_schedule(
    context: dg.ScheduleEvaluationContext,
) -> list[dg.RunRequest]:
    """Daily compaction schedule - generates run requests for all known feeds.

    Runs at 2am UTC to process yesterday's data, giving a 2-hour buffer after
    UTC midnight for any late-arriving data.

    Note: The feed_discovery_sensor handles adding new feeds to dynamic partitions.
    This schedule ensures all known feeds are processed even if the sensor missed them.
    """
    # Get yesterday's date as the partition key
    scheduled_date = context.scheduled_execution_time
    partition_date = (scheduled_date - timedelta(days=1)).strftime("%Y-%m-%d")

    # Get all known feed partitions
    # Use literal "feed" to match feed_partitions.name for type safety
    known_feeds = list(context.instance.get_dynamic_partitions("feed"))

    if not known_feeds:
        context.log.warning(
            f"No feed partitions registered for {partition_date}. "
            "The feed_discovery_sensor should populate these."
        )
        return []

    context.log.info(f"Scheduling {len(known_feeds)} compaction runs for {partition_date}")

    # Create run request for each feed
    return [
        dg.RunRequest(
            run_key=f"schedule_{partition_date}_{feed}",
            partition_key=dg.MultiPartitionKey({"date": partition_date, "feed": feed}),
        )
        for feed in known_feeds
    ]
