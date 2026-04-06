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


class BucketScanResult:
    """Results from scanning the parquet bucket in a single pass."""

    def __init__(self) -> None:
        self.rt_parquet_files: list[dict[str, Any]] = []
        self.schedule_metadata: list[dict[str, str]] = []  # [{path, base64url, feed_digest}]


# RT pattern: {feed_type}/date={YYYY-MM-DD}/base64url={encoded}/data.parquet
_RT_PATTERN = re.compile(
    r"^(?P<feed_type>[^/]+)/date=(?P<date>\d{4}-\d{2}-\d{2})/base64url=(?P<base64url>[A-Za-z0-9_-]+)/data\.parquet$"
)

# Schedule pattern: schedules/base64url={encoded}/_feed_digest={hash}/metadata.json
_SCHEDULE_PATTERN = re.compile(
    r"^schedules/base64url=(?P<base64url>[A-Za-z0-9_-]+)/_feed_digest=(?P<feed_digest>[^/]+)/metadata\.json$"
)


def scan_bucket(
    client: storage.Client,
    bucket_name: str,
) -> BucketScanResult:
    """Single-pass scan of the parquet bucket for RT data files and schedule metadata."""
    bucket = client.bucket(bucket_name)
    result = BucketScanResult()

    for blob in bucket.list_blobs():
        name = blob.name

        if name.endswith("data.parquet"):
            match = _RT_PATTERN.match(name)
            if match:
                result.rt_parquet_files.append(
                    {
                        "path": name,
                        "feed_type": match.group("feed_type"),
                        "date": match.group("date"),
                        "base64url": match.group("base64url"),
                        "size_bytes": blob.size or 0,
                    }
                )
        elif name.endswith("metadata.json") and name.startswith("schedules/"):
            match = _SCHEDULE_PATTERN.match(name)
            if match:
                result.schedule_metadata.append(
                    {
                        "path": name,
                        "base64url": match.group("base64url"),
                        "feed_digest": match.group("feed_digest"),
                    }
                )

    return result


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

    # Step 2: Single-pass bucket scan for RT parquet + schedule metadata
    context.log.info(f"Scanning gs://{gcs.parquet_bucket}")
    scan = scan_bucket(client, gcs.parquet_bucket)
    parquet_files = scan.rt_parquet_files
    context.log.info(
        f"Found {len(parquet_files)} RT parquet files, "
        f"{len(scan.schedule_metadata)} schedule versions"
    )

    if not parquet_files and not scan.schedule_metadata:
        context.log.info("No data found, writing empty inventories")
        _upload_json(client, gcs.parquet_bucket, "inventory.json", [])
        _upload_json(client, gcs.parquet_bucket, "schedules.json", [])
        return dg.Output(
            {"feeds_count": 0, "files_processed": 0, "schedule_feeds": 0, "schedule_versions": 0},
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

    # Step 6: Upload RT inventory
    _upload_json(client, gcs.parquet_bucket, "inventory.json", feeds_output)
    context.log.info(f"Wrote inventory.json with {len(feeds_output)} RT feeds")

    # Step 7: Build and upload schedule inventory
    schedule_feeds = _build_schedule_inventory(client, gcs.parquet_bucket, scan.schedule_metadata)
    _upload_json(client, gcs.parquet_bucket, "schedules.json", schedule_feeds)
    schedule_versions = sum(len(f.get("versions", [])) for f in schedule_feeds)
    context.log.info(
        f"Wrote schedules.json with {len(schedule_feeds)} feeds, {schedule_versions} versions"
    )

    return dg.Output(
        {
            "feeds_count": len(feeds_output),
            "files_processed": len(parquet_files),
            "schedule_feeds": len(schedule_feeds),
            "schedule_versions": schedule_versions,
        },
        metadata={
            "feeds_count": len(feeds_output),
            "files_processed": len(parquet_files),
            "total_records": total_records,
            "total_bytes": total_bytes,
            "schedule_feeds": len(schedule_feeds),
            "schedule_versions": schedule_versions,
        },
    )


def _build_schedule_inventory(
    client: storage.Client,
    bucket_name: str,
    schedule_metadata: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Read schedule metadata.json files and group by schedule URL.

    Returns list of dicts: {schedule_url, base64url, versions: [{_feed_digest, date_retrieved, ...}]}
    """
    bucket = client.bucket(bucket_name)

    by_url: dict[str, dict[str, Any]] = {}

    for entry in schedule_metadata:
        blob = bucket.blob(entry["path"])
        try:
            content = blob.download_as_text()
            meta = json.loads(content)
        except Exception:
            continue

        schedule_url = meta.get("schedule_url", "")
        if not schedule_url:
            continue

        base64url = entry["base64url"]

        if schedule_url not in by_url:
            by_url[schedule_url] = {
                "schedule_url": schedule_url,
                "base64url": base64url,
                "versions": [],
            }

        by_url[schedule_url]["versions"].append(
            {
                "_feed_digest": meta.get("_feed_digest", ""),
                "date_retrieved": meta.get("date_retrieved", ""),
                "feed_start_date": meta.get("feed_start_date"),
                "feed_end_date": meta.get("feed_end_date"),
            }
        )

    # Sort versions by date_retrieved within each feed
    result = []
    for feed in sorted(by_url.values(), key=lambda f: f["schedule_url"]):
        feed["versions"] = sorted(feed["versions"], key=lambda v: v.get("date_retrieved", ""))
        result.append(feed)

    return result


def _upload_json(
    client: storage.Client,
    bucket_name: str,
    filename: str,
    data: list[dict[str, Any]],
) -> None:
    """Upload a JSON file to bucket root."""
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(filename)
    content = json.dumps(data, indent=2)
    blob.upload_from_string(content, content_type="application/json")
