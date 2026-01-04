"""Sensors for the compaction pipeline."""

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import dagster as dg

from dagster_pipeline.defs.assets import (
    service_alerts_parquet,
    trip_updates_parquet,
    vehicle_positions_parquet,
)
from dagster_pipeline.defs.assets.compaction import (
    decode_base64url,
    discover_feed_urls,
    url_to_partition_key,
)
from dagster_pipeline.defs.partitions import (
    service_alerts_feeds,
    trip_updates_feeds,
    vehicle_positions_feeds,
)
from dagster_pipeline.defs.resources import GCSResource


class FeedTypeConfig(NamedTuple):
    """Configuration for a feed type."""

    feed_type: str  # GCS path component
    partition_name: str  # Name of the dynamic partition definition
    partition_def: dg.DynamicPartitionsDefinition  # For building add requests
    asset: dg.AssetsDefinition


# Configuration mapping feed types to their partitions and assets
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


@dg.sensor(
    asset_selection=[
        vehicle_positions_parquet,
        trip_updates_parquet,
        service_alerts_parquet,
    ],
    minimum_interval_seconds=300,  # 5 minutes
    default_status=dg.DefaultSensorStatus.STOPPED,  # Start disabled for safe rollout
)
def feed_discovery_sensor(
    context: dg.SensorEvaluationContext,
    gcs: GCSResource,
) -> dg.SensorResult:
    """Discover new feeds from GCS and add to dynamic partitions.

    This sensor:
    1. Scans GCS for feeds in yesterday's data, per feed type
    2. Adds any new feeds to the type-specific dynamic partition
    3. Triggers runs for discovered feed+date combinations

    Note: Starts in STOPPED status for safe rollout. Enable after populating
    initial feed partitions.
    """
    client = gcs.get_client()

    # Process yesterday's data (same logic as schedule)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")

    run_requests: list[dg.RunRequest] = []
    dynamic_partitions_requests: list[dg.AddDynamicPartitionsRequest] = []

    # Process each feed type separately
    for config in FEED_TYPE_CONFIGS:
        # Discover feeds for this type (returns base64url-encoded)
        discovered_base64 = discover_feed_urls(
            client, gcs.protobuf_bucket, config.feed_type, yesterday
        )

        if not discovered_base64:
            context.log.info(f"No {config.feed_type} feeds found for {yesterday}")
            continue

        # Convert to partition keys (HTTPS clean, HTTP with ~ prefix)
        discovered_keys = {url_to_partition_key(decode_base64url(b64)) for b64 in discovered_base64}

        # Get currently known feeds for this type
        known_feeds = set(context.instance.get_dynamic_partitions(config.partition_name))
        new_feeds = discovered_keys - known_feeds

        context.log.info(
            f"Discovered {len(discovered_keys)} {config.feed_type} feeds for {yesterday}, "
            f"{len(new_feeds)} are new, {len(known_feeds)} already known"
        )

        # Add new feeds to this type's partition
        if new_feeds:
            dynamic_partitions_requests.append(
                config.partition_def.build_add_request(list(new_feeds))
            )

        # Create run requests for this type's asset
        for feed_key in discovered_keys:
            multi_key = dg.MultiPartitionKey({"date": yesterday, "feed": feed_key})
            run_requests.append(
                dg.RunRequest(
                    run_key=f"{config.feed_type}_{yesterday}_{feed_key}",
                    asset_selection=[config.asset.key],
                    partition_key=multi_key,
                )
            )

    if not run_requests:
        context.log.info(f"No feeds found for {yesterday}")

    return dg.SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=dynamic_partitions_requests,
    )
