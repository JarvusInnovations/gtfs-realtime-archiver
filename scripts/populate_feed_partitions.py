#!/usr/bin/env python3
"""One-time migration script to populate feed partitions from existing GCS data.

This script scans the protobuf bucket for existing feeds and adds them
to Dagster's dynamic partitions store.

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


def discover_all_feeds(
    client: storage.Client,
    bucket_name: str,
    days: int,
) -> dict[str, set[str]]:
    """Discover all unique feeds from GCS across multiple days.

    Args:
        client: GCS client
        bucket_name: Protobuf bucket name
        days: Number of days to scan

    Returns:
        Dict mapping stripped URL to set of dates where it was found
    """
    feeds_by_date: dict[str, set[str]] = {}

    for days_ago in range(days):
        date = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        print(f"  Scanning {date}...", end=" ", flush=True)

        date_feeds: set[str] = set()
        for feed_type in ["vehicle_positions", "trip_updates", "service_alerts"]:
            base64_feeds = discover_feed_urls(client, bucket_name, feed_type, date)
            for b64 in base64_feeds:
                try:
                    partition_key = url_to_partition_key(decode_base64url(b64))
                    date_feeds.add(partition_key)
                except Exception as e:
                    print(f"\n    Warning: Failed to decode {b64}: {e}")

        print(f"found {len(date_feeds)} feeds")

        for feed in date_feeds:
            if feed not in feeds_by_date:
                feeds_by_date[feed] = set()
            feeds_by_date[feed].add(date)

    return feeds_by_date


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
    feeds_by_date = discover_all_feeds(client, bucket_name, args.days)

    if not feeds_by_date:
        print("\nNo feeds found in GCS data.")
        sys.exit(0)

    print(f"\nDiscovered {len(feeds_by_date)} unique feeds:")
    for feed, dates in sorted(feeds_by_date.items()):
        print(f"  - {feed} (seen on {len(dates)} days)")

    if args.dry_run:
        print("\n[DRY RUN] Would add the above feeds to Dagster dynamic partitions.")
        print("Run without --dry-run to apply changes.")
        sys.exit(0)

    # Add to Dagster dynamic partitions
    print("\nAdding feeds to Dagster dynamic partitions...")

    instance = DagsterInstance.get()

    # Get existing partitions
    existing = set(instance.get_dynamic_partitions("feed"))
    new_feeds = set(feeds_by_date.keys()) - existing

    if not new_feeds:
        print("All feeds already registered. Nothing to add.")
        sys.exit(0)

    print(f"  {len(existing)} feeds already registered")
    print(f"  {len(new_feeds)} new feeds to add")

    # Add new partitions
    instance.add_dynamic_partitions(
        partitions_def_name="feed",
        partition_keys=list(new_feeds),
    )

    print(f"\nSuccessfully added {len(new_feeds)} feed partitions!")
    print("\nNext steps:")
    print("  1. Enable the feed_discovery_sensor in Dagster UI")
    print(
        "  2. Or run: uv run dg launch --assets vehicle_positions_parquet --partition '2026-01-03|example.com/feed'"
    )


if __name__ == "__main__":
    main()
