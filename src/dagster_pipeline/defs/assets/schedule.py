"""GTFS Schedule ingestion assets.

Two assets:
- gtfs_schedule_check: daily unpartitioned check for new feed versions
- gtfs_schedule_ingest: partitioned by schedule URL, writes new versions as exploded parquet
"""

import hashlib

import dagster as dg
import requests
import yaml

from dagster_pipeline.defs.assets.compaction import (
    decode_base64url,
    encode_base64url,
    url_to_partition_key,
    partition_key_to_url,
)
from dagster_pipeline.defs.resources import GCSResource, SecretManagerResource
from gtfs_digester import GTFSArchive, write_exploded, version_exists, list_versions
from gtfs_rt_archiver.config import flatten_agencies
from gtfs_rt_archiver.models import AgenciesFileConfig, AuthConfig, AuthType

# Dynamic partitions for schedule feeds — keyed by stripped URL (same as RT feeds)
schedule_feed_partitions = dg.DynamicPartitionsDefinition(name="schedule_feeds")


def _resolve_schedule_auth(
    config: AgenciesFileConfig,
) -> dict[str, AuthConfig | None]:
    """Build a map of schedule_url -> auth config from agencies.yaml."""
    feeds = flatten_agencies(config)
    url_to_auth: dict[str, AuthConfig | None] = {}
    for feed in feeds:
        for url in feed.schedule_urls:
            url_str = str(url)
            if url_str not in url_to_auth:
                url_to_auth[url_str] = feed.auth
    return url_to_auth


def _download_schedule(url: str, auth: AuthConfig | None, timeout: int = 60) -> bytes:
    """Download a GTFS schedule zip, applying auth if needed."""
    headers = {}
    params = {}

    if auth and auth.resolved_value:
        if auth.type == AuthType.HEADER:
            headers[auth.key] = auth.resolved_value
        elif auth.type == AuthType.QUERY:
            params[auth.key] = auth.resolved_value

    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _load_agencies_config(secret_manager: SecretManagerResource) -> AgenciesFileConfig:
    """Load and parse agencies.yaml from Secret Manager."""
    agencies_yaml = secret_manager.get_secret()
    raw_config = yaml.safe_load(agencies_yaml)
    return AgenciesFileConfig.model_validate(raw_config)


@dg.asset(
    compute_kind="python",
    group_name="schedule",
    description="Check all schedule URLs for new feed versions",
)
def gtfs_schedule_check(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
    secret_manager: SecretManagerResource,
) -> dg.Output[dict]:
    """Daily check for new GTFS schedule feed versions.

    For each unique schedule_url in agencies.yaml:
    1. Download the zip
    2. Run gtfs-digester to compute fingerprint
    3. Check if this fingerprint already exists in the parquet bucket
    4. If new: register the URL as a dynamic partition (if not already) and record the discovery
    """
    config = _load_agencies_config(secret_manager)
    url_auth = _resolve_schedule_auth(config)

    # TODO: resolve auth secrets via Secret Manager for feeds that need them

    unique_urls = sorted(url_auth.keys())
    context.log.info(f"Checking {len(unique_urls)} unique schedule URLs")

    new_feeds: list[dict] = []
    unchanged = 0
    errors = 0

    partition_requests = []

    for url in unique_urls:
        b64 = encode_base64url(url)
        base_path = f"gs://{gcs.parquet_bucket}/schedules/base64url={b64}"
        partition_key = url_to_partition_key(url)

        try:
            auth = url_auth[url]
            zip_bytes = _download_schedule(url, auth)
            source_sha256 = hashlib.sha256(zip_bytes).hexdigest()

            archive = GTFSArchive.from_zip(zip_bytes)
            fp = archive.fingerprint.root_hash

            if version_exists(base_path, fp):
                context.log.debug(f"Unchanged: {url} -> {fp[:24]}...")
                unchanged += 1
                continue

            new_feeds.append({
                "schedule_url": url,
                "base64url": b64,
                "fingerprint": fp,
                "source_sha256": source_sha256,
                "partition_key": partition_key,
            })

            # Register partition if new
            partition_requests.append(
                schedule_feed_partitions.build_add_request([partition_key])
            )

            context.log.info(f"New feed: {url} -> {fp[:24]}...")

        except Exception as e:
            context.log.error(f"Error checking {url}: {e}")
            errors += 1

    result = {
        "urls_checked": len(unique_urls),
        "new_feeds": len(new_feeds),
        "unchanged": unchanged,
        "errors": errors,
        "new_feed_details": new_feeds,
    }

    return dg.Output(
        result,
        metadata={
            "urls_checked": len(unique_urls),
            "new_feeds": len(new_feeds),
            "unchanged": unchanged,
            "errors": errors,
        },
    )


@dg.asset(
    compute_kind="python",
    group_name="schedule",
    partitions_def=schedule_feed_partitions,
    description="Ingest the latest GTFS schedule version for a feed URL as exploded parquet",
)
def gtfs_schedule_ingest(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
    secret_manager: SecretManagerResource,
) -> dg.Output[dict]:
    """Ingest the current GTFS schedule for a URL.

    Partition key is a stripped URL (same format as RT feeds).
    Downloads fresh, digests, writes exploded parquet if the fingerprint is new.
    """
    partition_key = context.partition_key
    schedule_url = partition_key_to_url(partition_key)
    b64 = encode_base64url(schedule_url)
    base_path = f"gs://{gcs.parquet_bucket}/schedules/base64url={b64}"

    # Resolve auth
    config = _load_agencies_config(secret_manager)
    url_auth = _resolve_schedule_auth(config)
    auth = url_auth.get(schedule_url)

    # Download and digest
    context.log.info(f"Downloading: {schedule_url}")
    zip_bytes = _download_schedule(schedule_url, auth)
    source_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    archive = GTFSArchive.from_zip(zip_bytes)
    fp = archive.fingerprint.root_hash

    # Check if already ingested (idempotent)
    if version_exists(base_path, fp):
        context.log.info(f"Already ingested: {schedule_url} -> {fp[:24]}...")
        return dg.Output(
            {"status": "already_exists", "fingerprint": fp},
            metadata={"status": "already_exists", "fingerprint": fp},
        )

    # Write exploded parquet
    context.log.info(f"Writing: {base_path}/_feed_digest={fp[:24]}...")
    from datetime import UTC, datetime

    metadata = write_exploded(
        archive,
        base_path=base_path,
        schedule_url=schedule_url,
        date_retrieved=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source_sha256=source_sha256,
    )

    result = {
        "status": "ingested",
        "fingerprint": fp,
        "schedule_url": schedule_url,
        "files": len(archive.filenames),
        "feed_start_date": metadata.feed_start_date,
        "feed_end_date": metadata.feed_end_date,
    }

    context.log.info(
        f"Ingested {len(archive.filenames)} files "
        f"(service: {metadata.feed_start_date} to {metadata.feed_end_date})"
    )

    return dg.Output(
        result,
        metadata={
            "fingerprint": fp,
            "schedule_url": schedule_url,
            "files": len(archive.filenames),
            "feed_start_date": metadata.feed_start_date or "",
            "feed_end_date": metadata.feed_end_date or "",
        },
    )
