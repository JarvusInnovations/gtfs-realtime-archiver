"""Schedules for the compaction pipeline."""

from datetime import timedelta

import dagster as dg

from dagster_pipeline.defs.assets import (
    bucket_inventory,
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)
from dagster_pipeline.defs.partitions import (
    service_alerts_feeds,
    service_alerts_partitions,
    trip_updates_feeds,
    trip_updates_partitions,
    vehicle_positions_feeds,
    vehicle_positions_partitions,
)
from dagster_pipeline.defs.sensors import FeedTypeConfig

# Configuration for per-type jobs and schedules
FEED_TYPE_CONFIGS = [
    FeedTypeConfig(
        "vehicle_positions",
        "vehicle_positions_feeds",
        vehicle_positions_feeds,
        vehicle_positions_parquet,
    ),
    FeedTypeConfig(
        "trip_updates",
        "trip_updates_feeds",
        trip_updates_feeds,
        trip_updates_parquet,
    ),
    FeedTypeConfig(
        "service_alerts",
        "service_alerts_feeds",
        service_alerts_feeds,
        service_alerts_parquet,
    ),
]

# Define per-type jobs for daily compaction
vehicle_positions_compaction_job = dg.define_asset_job(
    name="vehicle_positions_compaction_job",
    selection=[vehicle_positions_parquet],
    partitions_def=vehicle_positions_partitions,
)

trip_updates_compaction_job = dg.define_asset_job(
    name="trip_updates_compaction_job",
    selection=[trip_updates_parquet],
    partitions_def=trip_updates_partitions,
)

service_alerts_compaction_job = dg.define_asset_job(
    name="service_alerts_compaction_job",
    selection=[service_alerts_parquet],
    partitions_def=service_alerts_partitions,
)


@dg.schedule(
    job=vehicle_positions_compaction_job,
    cron_schedule="0 2 * * *",  # 2am UTC daily
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def vehicle_positions_schedule(
    context: dg.ScheduleEvaluationContext,
) -> list[dg.RunRequest]:
    """Daily vehicle positions compaction schedule."""
    return _create_run_requests(context, FEED_TYPE_CONFIGS[0])


@dg.schedule(
    job=trip_updates_compaction_job,
    cron_schedule="0 2 * * *",  # 2am UTC daily
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def trip_updates_schedule(
    context: dg.ScheduleEvaluationContext,
) -> list[dg.RunRequest]:
    """Daily trip updates compaction schedule."""
    return _create_run_requests(context, FEED_TYPE_CONFIGS[1])


@dg.schedule(
    job=service_alerts_compaction_job,
    cron_schedule="0 2 * * *",  # 2am UTC daily
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def service_alerts_schedule(
    context: dg.ScheduleEvaluationContext,
) -> list[dg.RunRequest]:
    """Daily service alerts compaction schedule."""
    return _create_run_requests(context, FEED_TYPE_CONFIGS[2])


def _create_run_requests(
    context: dg.ScheduleEvaluationContext,
    config: FeedTypeConfig,
) -> list[dg.RunRequest]:
    """Create run requests for a feed type's known feeds.

    Runs at 2am UTC to process yesterday's data, giving a 2-hour buffer after
    UTC midnight for any late-arriving data.

    Note: The feed_discovery_sensor handles adding new feeds to dynamic partitions.
    This schedule ensures all known feeds are processed even if the sensor missed them.
    """
    # Get yesterday's date as the partition key
    scheduled_date = context.scheduled_execution_time
    partition_date = (scheduled_date - timedelta(days=1)).strftime("%Y-%m-%d")

    # Get known feeds for this type
    known_feeds = list(context.instance.get_dynamic_partitions(config.partition_name))

    if not known_feeds:
        context.log.warning(
            f"No {config.feed_type} feed partitions registered for {partition_date}. "
            "The feed_discovery_sensor should populate these."
        )
        return []

    context.log.info(
        f"Scheduling {len(known_feeds)} {config.feed_type} compaction runs for {partition_date}"
    )

    # Create run request for each feed
    return [
        dg.RunRequest(
            run_key=f"schedule_{config.feed_type}_{partition_date}_{feed}",
            partition_key=dg.MultiPartitionKey({"date": partition_date, "feed": feed}),
        )
        for feed in known_feeds
    ]


# Job for inventory generation
inventory_job = dg.define_asset_job(
    name="inventory_job",
    selection=[bucket_inventory],
)


@dg.schedule(
    job=inventory_job,
    cron_schedule="0 4 * * *",  # 4am UTC (2 hours after compaction starts)
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
def bucket_inventory_schedule(
    context: dg.ScheduleEvaluationContext,
) -> dg.RunRequest:
    """Daily bucket inventory generation schedule.

    Runs at 4am UTC, 2 hours after compaction jobs start, to allow
    time for parquet file generation to complete.
    """
    return dg.RunRequest()
