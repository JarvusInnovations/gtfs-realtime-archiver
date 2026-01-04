"""Sensors for the compaction pipeline."""

from datetime import UTC, datetime, timedelta

import dagster as dg
from google.cloud import storage

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
from dagster_pipeline.defs.partitions import feed_partitions
from dagster_pipeline.defs.resources import GCSResource


def discover_all_feeds_for_date(
    client: storage.Client,
    bucket_name: str,
    date: str,
) -> set[str]:
    """Discover all unique feeds across all feed types for a date.

    Args:
        client: GCS client
        bucket_name: Source bucket name
        date: Date string in YYYY-MM-DD format

    Returns:
        Set of base64url-encoded feed URLs found for this date
    """
    all_feeds: set[str] = set()
    for feed_type in ["vehicle_positions", "trip_updates", "service_alerts"]:
        feeds = discover_feed_urls(client, bucket_name, feed_type, date)
        all_feeds.update(feeds)
    return all_feeds


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
    1. Scans GCS for feeds in yesterday's data
    2. Adds any new feeds to the dynamic partition
    3. Triggers runs for discovered feed+date combinations

    Note: Starts in STOPPED status for safe rollout. Enable after populating
    initial feed partitions.
    """
    client = gcs.get_client()

    # Process yesterday's data (same logic as schedule)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")

    # Discover feeds in GCS (returns base64url-encoded)
    discovered_base64 = discover_all_feeds_for_date(client, gcs.protobuf_bucket, yesterday)

    if not discovered_base64:
        context.log.info(f"No feeds found for {yesterday}")
        return dg.SensorResult(
            run_requests=[],
            dynamic_partitions_requests=[],
        )

    # Convert to partition keys (HTTPS clean, HTTP with ~ prefix)
    discovered_keys = {url_to_partition_key(decode_base64url(b64)) for b64 in discovered_base64}

    # Get currently known feeds
    # Use literal "feed" to match feed_partitions.name for type safety
    known_feeds = set(context.instance.get_dynamic_partitions("feed"))
    new_feeds = discovered_keys - known_feeds

    context.log.info(
        f"Discovered {len(discovered_keys)} feeds for {yesterday}, "
        f"{len(new_feeds)} are new, {len(known_feeds)} already known"
    )

    # Build dynamic partition add request for new feeds
    dynamic_partitions_requests = []
    if new_feeds:
        dynamic_partitions_requests.append(feed_partitions.build_add_request(list(new_feeds)))

    # Create run requests for all discovered feeds for yesterday
    # This ensures we process any feeds that may have failed previously
    run_requests = []
    for feed_key in discovered_keys:
        multi_key = dg.MultiPartitionKey({"date": yesterday, "feed": feed_key})
        run_requests.append(
            dg.RunRequest(
                run_key=f"compaction_{yesterday}_{feed_key}",
                partition_key=multi_key,
            )
        )

    return dg.SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=dynamic_partitions_requests,
    )
