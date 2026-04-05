"""GTFS Schedule ingestion assets.

Two assets:
- gtfs_schedule_check: daily unpartitioned check for new feed versions
- gtfs_schedule_ingest: partitioned write of new versions as exploded parquet
"""

import hashlib
import io

import dagster as dg
import requests
import yaml

from dagster_pipeline.defs.assets.compaction import encode_base64url
from dagster_pipeline.defs.resources import GCSResource, SecretManagerResource
from gtfs_digester import GTFSArchive, write_exploded, version_exists
from gtfs_rt_archiver.config import flatten_agencies
from gtfs_rt_archiver.models import AgenciesFileConfig, AuthConfig, AuthType

# Dynamic partitions for schedule ingestion: keyed by "{base64url}:{fingerprint}"
schedule_feed_partitions = dg.DynamicPartitionsDefinition(name="schedule_feeds")


def _resolve_schedule_auth(
    config: AgenciesFileConfig,
) -> dict[str, AuthConfig | None]:
    """Build a map of schedule_url -> auth config from agencies.yaml.

    Since schedule URLs may need the same auth as their RT feeds,
    we resolve auth through the feed flattening and map it back.
    """
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
    4. If new: register a dynamic partition for ingestion
    """
    # Load agencies config from Secret Manager
    agencies_yaml = secret_manager.get_secret()
    raw_config = yaml.safe_load(agencies_yaml)
    config = AgenciesFileConfig.model_validate(raw_config)

    # Resolve auth for schedule URLs
    url_auth = _resolve_schedule_auth(config)

    # Resolve secrets for URLs that need auth
    # (In production, secrets are pre-resolved by the archiver service.
    # For Dagster, we need to resolve them here.)
    # TODO: resolve auth secrets via Secret Manager

    # Deduplicate schedule URLs
    unique_urls = sorted(url_auth.keys())
    context.log.info(f"Checking {len(unique_urls)} unique schedule URLs")

    new_feeds: list[dict] = []
    unchanged = 0
    errors = 0

    for url in unique_urls:
        b64 = encode_base64url(url)
        base_path = f"gs://{gcs.parquet_bucket}/schedules/base64url={b64}"

        try:
            # Download
            auth = url_auth[url]
            zip_bytes = _download_schedule(url, auth)

            # Compute SHA256 of raw zip for provenance
            source_sha256 = hashlib.sha256(zip_bytes).hexdigest()

            # Digest
            archive = GTFSArchive.from_zip(zip_bytes)
            fp = archive.fingerprint.root_hash

            # Check if already ingested
            if version_exists(base_path, fp):
                context.log.debug(f"Unchanged: {url} -> {fp[:24]}...")
                unchanged += 1
                continue

            # New version — register partition
            partition_key = f"{b64}:{fp}"
            context.instance.add_dynamic_partitions(
                partitions_def_name="schedule_feeds",
                partition_keys=[partition_key],
            )

            new_feeds.append({
                "schedule_url": url,
                "base64url": b64,
                "fingerprint": fp,
                "source_sha256": source_sha256,
                "partition_key": partition_key,
            })

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
    description="Ingest a new GTFS schedule version as exploded parquet",
)
def gtfs_schedule_ingest(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
    secret_manager: SecretManagerResource,
) -> dg.Output[dict]:
    """Ingest a single new GTFS schedule feed version.

    Partition key format: "{base64url}:{fingerprint}"

    Downloads the zip fresh, runs gtfs-digester, writes exploded parquet
    to gs://{parquet_bucket}/schedules/base64url={b64}/_feed_digest={fp}/
    """
    partition_key = context.partition_key
    b64, expected_fp = partition_key.split(":", 1)

    # Decode the schedule URL
    from dagster_pipeline.defs.assets.compaction import decode_base64url
    schedule_url = decode_base64url(b64)

    base_path = f"gs://{gcs.parquet_bucket}/schedules/base64url={b64}"

    # Check if already written (idempotent)
    if version_exists(base_path, expected_fp):
        context.log.info(f"Already ingested: {schedule_url} -> {expected_fp[:24]}...")
        return dg.Output(
            {"status": "already_exists", "fingerprint": expected_fp},
            metadata={"status": "already_exists"},
        )

    # Resolve auth for this URL
    agencies_yaml = secret_manager.get_secret()
    raw_config = yaml.safe_load(agencies_yaml)
    config = AgenciesFileConfig.model_validate(raw_config)
    url_auth = _resolve_schedule_auth(config)
    auth = url_auth.get(schedule_url)

    # Download fresh and digest
    context.log.info(f"Downloading: {schedule_url}")
    zip_bytes = _download_schedule(schedule_url, auth)
    source_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    archive = GTFSArchive.from_zip(zip_bytes)
    fp = archive.fingerprint.root_hash

    if fp != expected_fp:
        context.log.warning(
            f"Fingerprint mismatch: expected {expected_fp[:24]}... got {fp[:24]}... "
            f"(feed may have changed between check and ingest)"
        )

    # Write exploded parquet
    context.log.info(f"Writing exploded parquet to {base_path}/_feed_digest={fp[:24]}...")
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
        f"Ingested {len(archive.filenames)} files for {schedule_url} "
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
