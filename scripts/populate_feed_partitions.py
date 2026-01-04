#!/usr/bin/env python3
"""One-time migration script to populate feed partitions from existing GCS data.

This script scans the protobuf bucket for existing feeds and adds them
to Dagster's per-type dynamic partitions store.

Usage:
    # Scan last 7 days (default)
    uv run python scripts/populate_feed_partitions.py

    # Scan specific number of days
    uv run python scripts/populate_feed_partitions.py --days 30

    # Dry run (show what would be added without adding)
    uv run python scripts/populate_feed_partitions.py --dry-run

Environment variables required:
    - DAGSTER_HOME: Path to Dagster home directory
    - GCS_BUCKET_RT_PROTOBUF: GCS bucket containing protobuf archives
    - GCP_PROJECT_ID: (optional) GCP project ID
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

from dagster import DagsterInstance
from google.cloud import storage

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dagster_pipeline.defs.assets.compaction import (
    decode_base64url,
    discover_feed_urls,
    url_to_partition_key,
)

# Feed types and their partition names
FEED_TYPES = [
    ("vehicle_positions", "vehicle_positions_feeds"),
    ("trip_updates", "trip_updates_feeds"),
    ("service_alerts", "service_alerts_feeds"),
]


def discover_feeds_by_type(
    client: storage.Client,
    bucket_name: str,
    days: int,
) -> dict[str, dict[str, set[str]]]:
    """Discover feeds from GCS, organized by feed type.

    Args:
        client: GCS client
        bucket_name: Protobuf bucket name
        days: Number of days to scan

    Returns:
        Dict mapping feed_type to (dict mapping partition_key to set of dates)
    """
    feeds_by_type: dict[str, dict[str, set[str]]] = {feed_type: {} for feed_type, _ in FEED_TYPES}

    for days_ago in range(days):
        date = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        print(f"  Scanning {date}...", end=" ", flush=True)

        day_total = 0
        for feed_type, _ in FEED_TYPES:
            base64_feeds = discover_feed_urls(client, bucket_name, feed_type, date)
            for b64 in base64_feeds:
                try:
                    partition_key = url_to_partition_key(decode_base64url(b64))
                    if partition_key not in feeds_by_type[feed_type]:
                        feeds_by_type[feed_type][partition_key] = set()
                    feeds_by_type[feed_type][partition_key].add(date)
                    day_total += 1
                except Exception as e:
                    print(f"\n    Warning: Failed to decode {b64}: {e}")

        print(f"found {day_total} feed instances")

    return feeds_by_type


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate Dagster feed partitions from existing GCS data"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to scan (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without making changes",
    )
    args = parser.parse_args()

    # Check required environment variables
    dagster_home = os.environ.get("DAGSTER_HOME")
    bucket_name = os.environ.get("GCS_BUCKET_RT_PROTOBUF")

    if not dagster_home:
        print("Error: DAGSTER_HOME environment variable not set")
        print("Set it to an absolute path, e.g.: export DAGSTER_HOME=$PWD/.dagster_home")
        sys.exit(1)

    if not bucket_name:
        print("Error: GCS_BUCKET_RT_PROTOBUF environment variable not set")
        sys.exit(1)

    print(f"Dagster home: {dagster_home}")
    print(f"Protobuf bucket: {bucket_name}")
    print(f"Scanning last {args.days} days...")
    print()

    # Initialize GCS client
    client = storage.Client()

    # Discover feeds from GCS
    print("Discovering feeds from GCS:")
    feeds_by_type = discover_feeds_by_type(client, bucket_name, args.days)

    total_feeds = sum(len(feeds) for feeds in feeds_by_type.values())
    if total_feeds == 0:
        print("\nNo feeds found in GCS data.")
        sys.exit(0)

    print(f"\nDiscovered feeds by type:")
    for feed_type, partition_name in FEED_TYPES:
        feeds = feeds_by_type[feed_type]
        print(f"\n  {feed_type} ({len(feeds)} feeds):")
        for feed, dates in sorted(feeds.items()):
            print(f"    - {feed} (seen on {len(dates)} days)")

    if args.dry_run:
        print("\n[DRY RUN] Would add the above feeds to Dagster dynamic partitions.")
        print("Run without --dry-run to apply changes.")
        sys.exit(0)

    # Add to Dagster dynamic partitions
    print("\nAdding feeds to Dagster dynamic partitions...")

    instance = DagsterInstance.get()

    total_added = 0
    for feed_type, partition_name in FEED_TYPES:
        feeds = feeds_by_type[feed_type]
        if not feeds:
            print(f"  {feed_type}: no feeds to add")
            continue

        # Get existing partitions for this type
        existing = set(instance.get_dynamic_partitions(partition_name))
        new_feeds = set(feeds.keys()) - existing

        if not new_feeds:
            print(f"  {feed_type}: all {len(existing)} feeds already registered")
            continue

        print(f"  {feed_type}: {len(existing)} existing, adding {len(new_feeds)} new")

        # Add new partitions
        instance.add_dynamic_partitions(
            partitions_def_name=partition_name,
            partition_keys=list(new_feeds),
        )
        total_added += len(new_feeds)

    print(f"\nSuccessfully added {total_added} feed partitions!")
    print("\nNext steps:")
    print("  1. Enable the feed_discovery_sensor in Dagster UI")
    print(
        "  2. Or run: uv run dg launch --assets vehicle_positions_parquet "
        "--partition '2026-01-03|example.com/feed'"
    )


if __name__ == "__main__":
    main()
