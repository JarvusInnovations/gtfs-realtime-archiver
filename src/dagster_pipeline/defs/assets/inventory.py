"""Bucket inventory asset for discovering parquet files and generating inventory.json."""

import io
import json
import re
from typing import Any

import dagster as dg
import gcsfs  # type: ignore[import-untyped]
import pyarrow.parquet as pq
from google.cloud import storage

from dagster_pipeline.defs.resources import GCSResource


def list_parquet_files(
    client: storage.Client,
    bucket_name: str,
) -> list[dict[str, Any]]:
    """List all data.parquet files in the bucket with their sizes.

    Returns list of dicts with keys: path, feed_type, date, base64url, size_bytes
    """
    bucket = client.bucket(bucket_name)
    results: list[dict[str, Any]] = []

    # Pattern: {feed_type}/date={YYYY-MM-DD}/base64url={encoded}/data.parquet
    pattern = re.compile(
        r"^(?P<feed_type>[^/]+)/date=(?P<date>\d{4}-\d{2}-\d{2})/base64url=(?P<base64url>[A-Za-z0-9_-]+)/data\.parquet$"
    )

    for blob in bucket.list_blobs():
        if not blob.name.endswith("data.parquet"):
            continue

        match = pattern.match(blob.name)
        if match:
            results.append(
                {
                    "path": blob.name,
                    "feed_type": match.group("feed_type"),
                    "date": match.group("date"),
                    "base64url": match.group("base64url"),
                    "size_bytes": blob.size or 0,
                }
            )

    return results


def read_parquet_row_count(
    fs: gcsfs.GCSFileSystem,
    bucket_name: str,
    path: str,
) -> int:
    """Read row count from parquet file metadata using efficient range reads.

    Uses gcsfs to read only the parquet footer (~8KB) instead of the entire file.
    """
    gcs_path = f"{bucket_name}/{path}"
    metadata = pq.read_metadata(gcs_path, filesystem=fs)
    return int(metadata.num_rows)


def load_feeds_metadata(
    client: storage.Client,
    bucket_name: str,
) -> dict[str, dict[str, str | None]]:
    """Load feeds.parquet and return lookup by base64url.

    Returns dict mapping base64url -> {agency_id, agency_name, system_id, system_name, feed_type}
    """
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("feeds.parquet")

    if not blob.exists():
        return {}

    buffer = io.BytesIO()
    blob.download_to_file(buffer)
    buffer.seek(0)

    table = pq.read_table(
        buffer,
        columns=[
            "base64url",
            "url",
            "agency_id",
            "agency_name",
            "system_id",
            "system_name",
            "feed_type",
        ],
    )
    feeds: dict[str, dict[str, str | None]] = {}
    for row in table.to_pylist():
        feeds[row["base64url"]] = {
            "url": row["url"],
            "agency_id": row["agency_id"],
            "agency_name": row["agency_name"],
            "system_id": row["system_id"],
            "system_name": row["system_name"],
            "feed_type": row["feed_type"],
        }
    return feeds


@dg.asset(
    compute_kind="python",
    group_name="metadata",
    description="Bucket inventory JSON file for CLI tooling to discover available feeds and date ranges",
    deps=[dg.AssetKey("feeds_metadata")],
)
def bucket_inventory(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
) -> dg.Output[dict[str, int]]:
    """Generate inventory.json at bucket root summarizing all available parquet data.

    Lists all data.parquet blobs, reads their metadata for row counts,
    aggregates per feed URL, and writes inventory.json to bucket root.
    """
    client = gcs.get_client()
    fs = gcsfs.GCSFileSystem(project=gcs.project_id)

    # Step 1: Load feeds metadata for agency info lookup
    context.log.info("Loading feeds metadata from feeds.parquet")
    feeds_lookup = load_feeds_metadata(client, gcs.parquet_bucket)
    context.log.info(f"Loaded metadata for {len(feeds_lookup)} feeds")

    # Step 2: List all data.parquet files
    context.log.info(f"Listing parquet files in gs://{gcs.parquet_bucket}")
    parquet_files = list_parquet_files(client, gcs.parquet_bucket)
    context.log.info(f"Found {len(parquet_files)} parquet files")

    if not parquet_files:
        context.log.info("No parquet files found, writing empty inventory")
        _upload_inventory(client, gcs.parquet_bucket, [])
        return dg.Output(
            {"feeds_count": 0, "files_processed": 0},
            metadata={"feeds_count": 0, "files_processed": 0},
        )

    # Step 3: Read row counts for each file (uses range reads for efficiency)
    context.log.info("Reading parquet metadata for row counts")
    for i, pf in enumerate(parquet_files):
        if (i + 1) % 50 == 0:
            context.log.info(f"Progress: {i + 1}/{len(parquet_files)} files")
        path = str(pf["path"])
        pf["row_count"] = read_parquet_row_count(fs, gcs.parquet_bucket, path)

    # Step 4: Aggregate per feed (by base64url)
    # Group by base64url across all feed types
    feed_aggregates: dict[str, dict[str, Any]] = {}

    for pf in parquet_files:
        base64url = str(pf["base64url"])

        if base64url not in feed_aggregates:
            # Look up URL and agency info from feeds metadata
            feed_meta = feeds_lookup.get(base64url, {})
            feed_aggregates[base64url] = {
                "base64url": base64url,
                "agency_id": feed_meta.get("agency_id"),
                "agency_name": feed_meta.get("agency_name"),
                "system_id": feed_meta.get("system_id"),
                "system_name": feed_meta.get("system_name"),
                "feed_type": feed_meta.get("feed_type"),
                "dates": set(),
                "total_records": 0,
                "total_bytes": 0,
            }

        agg = feed_aggregates[base64url]
        agg["dates"].add(pf["date"])
        agg["total_records"] += pf["row_count"]
        agg["total_bytes"] += pf["size_bytes"]

    # Step 5: Build feeds array
    feeds_output: list[dict[str, Any]] = []
    total_records = 0
    total_bytes = 0

    for base64url, agg in feed_aggregates.items():
        feed_meta = feeds_lookup.get(base64url, {})
        url = feed_meta.get("url")

        if not url:
            context.log.warning(f"No URL mapping for base64url={base64url}, skipping")
            continue

        sorted_dates = sorted(agg["dates"])
        feeds_output.append(
            {
                "url": url,
                "base64url": base64url,
                "agency_id": agg["agency_id"],
                "agency_name": agg["agency_name"],
                "system_id": agg["system_id"],
                "system_name": agg["system_name"],
                "feed_type": agg["feed_type"],
                "date_min": sorted_dates[0] if sorted_dates else None,
                "date_max": sorted_dates[-1] if sorted_dates else None,
                "total_records": agg["total_records"],
                "total_bytes": agg["total_bytes"],
            }
        )
        total_records += agg["total_records"]
        total_bytes += agg["total_bytes"]

    # Step 6: Upload inventory
    _upload_inventory(client, gcs.parquet_bucket, feeds_output)

    output_path = f"gs://{gcs.parquet_bucket}/inventory.json"
    context.log.info(f"Wrote inventory with {len(feeds_output)} feeds to {output_path}")

    return dg.Output(
        {"feeds_count": len(feeds_output), "files_processed": len(parquet_files)},
        metadata={
            "feeds_count": len(feeds_output),
            "files_processed": len(parquet_files),
            "total_records": total_records,
            "total_bytes": total_bytes,
            "output_path": output_path,
        },
    )


def _upload_inventory(
    client: storage.Client,
    bucket_name: str,
    feeds: list[dict[str, Any]],
) -> None:
    """Upload inventory.json to bucket root."""
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("inventory.json")

    content = json.dumps(feeds, indent=2)
    blob.upload_from_string(content, content_type="application/json")
