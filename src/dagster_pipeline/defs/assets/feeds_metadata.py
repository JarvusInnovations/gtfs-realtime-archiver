"""Feeds metadata asset for exporting agency/feed configuration to Parquet."""

import io

import dagster as dg
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from dagster_pipeline.defs.assets.compaction import encode_base64url
from dagster_pipeline.defs.resources import GCSResource, SecretManagerResource
from gtfs_rt_archiver.config import flatten_agencies
from gtfs_rt_archiver.models import AgenciesFileConfig

FEEDS_SCHEMA = pa.schema(
    [
        pa.field("base64url", pa.string(), nullable=False),
        pa.field("url", pa.string(), nullable=False),
        pa.field("feed_type", pa.string(), nullable=False),
        pa.field("feed_id", pa.string(), nullable=False),
        pa.field("feed_name", pa.string(), nullable=False),
        pa.field("agency_id", pa.string(), nullable=False),
        pa.field("agency_name", pa.string(), nullable=False),
        pa.field("system_id", pa.string(), nullable=True),
        pa.field("system_name", pa.string(), nullable=True),
        pa.field("interval_seconds", pa.int32(), nullable=False),
        pa.field("schedule_url", pa.string(), nullable=True),
    ]
)


@dg.asset(
    compute_kind="pyarrow",
    group_name="metadata",
    description="Feed configuration metadata from agencies.yaml for joining with GTFS-RT data",
)
def feeds_metadata(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
    secret_manager: SecretManagerResource,
) -> dg.Output[dict[str, int]]:
    """Export feed metadata from agencies.yaml to feeds.parquet.

    Reads the agencies configuration from Secret Manager, flattens the feed
    hierarchy, computes base64url for each feed URL, and writes to Parquet.
    """
    # Read agencies.yaml from Secret Manager
    context.log.info("Reading agencies configuration from Secret Manager")
    agencies_yaml = secret_manager.get_secret()
    raw_config = yaml.safe_load(agencies_yaml)
    config = AgenciesFileConfig.model_validate(raw_config)

    # Flatten the agency hierarchy to get all feeds
    feeds = flatten_agencies(config)
    context.log.info(f"Found {len(feeds)} feeds to export")

    # Build records for Parquet
    records = []
    for feed in feeds:
        url_str = str(feed.url)
        records.append(
            {
                "base64url": encode_base64url(url_str),
                "url": url_str,
                "feed_type": feed.feed_type.value,
                "feed_id": feed.id,
                "feed_name": feed.name,
                "agency_id": feed.agency_id,
                "agency_name": feed.agency_name,
                "system_id": feed.system_id,
                "system_name": feed.system_name,
                "interval_seconds": feed.interval_seconds,
                "schedule_url": str(feed.schedule_url) if feed.schedule_url else None,
            }
        )

    # Write to Parquet buffer
    table = pa.Table.from_pylist(records, schema=FEEDS_SCHEMA)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd", compression_level=9)
    buffer.seek(0)

    # Upload to GCS
    client = gcs.get_client()
    bucket = client.bucket(gcs.parquet_bucket)
    blob = bucket.blob("feeds.parquet")
    blob.upload_from_file(buffer, content_type="application/octet-stream")

    output_path = f"gs://{gcs.parquet_bucket}/feeds.parquet"
    context.log.info(f"Wrote {len(records)} feeds to {output_path}")

    return dg.Output(
        {"feeds_count": len(records)},
        metadata={
            "feeds_count": len(records),
            "output_path": output_path,
        },
    )
