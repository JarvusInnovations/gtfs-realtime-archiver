"""Compaction assets for converting protobuf archives to Parquet."""

import base64
import io
import re
from collections.abc import Iterator
from typing import Any

import dagster as dg
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets.schemas import (
    SERVICE_ALERTS_SCHEMA,
    TRIP_UPDATES_SCHEMA,
    VEHICLE_POSITIONS_SCHEMA,
)
from dagster_pipeline.defs.partitions import compaction_partitions
from dagster_pipeline.defs.resources import GCSResource


def decode_base64url(encoded: str) -> str:
    """Decode base64url string (add padding back for decoding)."""
    padded = encoded + "=" * (4 - len(encoded) % 4) if len(encoded) % 4 else encoded
    return base64.urlsafe_b64decode(padded).decode("utf-8")


def encode_base64url(url: str) -> str:
    """Encode URL to base64url (for GCS path lookup)."""
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


# Prefix for HTTP-only feeds (HTTPS is the default, no prefix needed)
HTTP_FEED_PREFIX = "~"


def url_to_partition_key(url: str) -> str:
    """Convert URL to partition key.

    HTTPS URLs (common): strip scheme, no prefix
    HTTP URLs (rare): strip scheme, add ~ prefix

    Examples:
        https://example.com/feed -> example.com/feed
        http://example.com/feed -> ~example.com/feed
    """
    if url.startswith("http://"):
        return HTTP_FEED_PREFIX + url[7:]  # len("http://") = 7
    elif url.startswith("https://"):
        return url[8:]  # len("https://") = 8
    return url  # No scheme, return as-is


def partition_key_to_url(key: str) -> str:
    """Convert partition key back to full URL.

    Examples:
        example.com/feed -> https://example.com/feed
        ~example.com/feed -> http://example.com/feed
    """
    if key.startswith(HTTP_FEED_PREFIX):
        return "http://" + key[1:]
    return "https://" + key


def discover_feed_urls(
    client: storage.Client,
    bucket_name: str,
    feed_type: str,
    date: str,
) -> set[str]:
    """Discover all unique base64url feed identifiers for a given date.

    Args:
        client: GCS client
        bucket_name: Source bucket name
        feed_type: Feed type (vehicle_positions, trip_updates, service_alerts)
        date: Date string in YYYY-MM-DD format

    Returns:
        Set of base64url-encoded feed URLs found for this date
    """
    bucket = client.bucket(bucket_name)
    prefix = f"{feed_type}/date={date}/"

    feed_urls: set[str] = set()
    for blob in bucket.list_blobs(prefix=prefix):
        # Extract base64url from path
        # Pattern: {feed_type}/date=YYYY-MM-DD/hour=.../base64url={encoded}/...
        match = re.search(r"base64url=([A-Za-z0-9_-]+)/", blob.name)
        if match:
            feed_urls.add(match.group(1))

    return feed_urls


def list_pb_files(
    client: storage.Client,
    bucket_name: str,
    feed_type: str,
    date: str,
    feed_url_encoded: str,
) -> list[str]:
    """List all .pb files for a given date and feed across all hours.

    Args:
        client: GCS client
        bucket_name: Source bucket name
        feed_type: Feed type
        date: Date string in YYYY-MM-DD format
        feed_url_encoded: Base64url-encoded feed URL

    Returns:
        Sorted list of blob names for .pb files
    """
    bucket = client.bucket(bucket_name)
    prefix = f"{feed_type}/date={date}/"

    pb_files = []
    for blob in bucket.list_blobs(prefix=prefix):
        if f"base64url={feed_url_encoded}/" in blob.name and blob.name.endswith(".pb"):
            pb_files.append(blob.name)

    return sorted(pb_files)


def parse_protobuf(content: bytes) -> gtfs_realtime_pb2.FeedMessage:
    """Parse protobuf content into FeedMessage."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(content)
    return feed


def extract_vehicle_positions(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
) -> Iterator[dict[str, Any]]:
    """Extract vehicle positions from a FeedMessage."""
    feed_timestamp = feed.header.timestamp if feed.header.timestamp else None

    for entity in feed.entity:
        if entity.HasField("vehicle"):
            vp = entity.vehicle

            yield {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "entity_id": entity.id,
                # Trip descriptor
                "trip_id": vp.trip.trip_id if vp.HasField("trip") else None,
                "route_id": vp.trip.route_id if vp.HasField("trip") else None,
                "direction_id": (
                    vp.trip.direction_id
                    if vp.HasField("trip") and vp.trip.HasField("direction_id")
                    else None
                ),
                "start_time": vp.trip.start_time if vp.HasField("trip") else None,
                "start_date": vp.trip.start_date if vp.HasField("trip") else None,
                "schedule_relationship": (
                    vp.trip.schedule_relationship if vp.HasField("trip") else None
                ),
                # Vehicle descriptor
                "vehicle_id": vp.vehicle.id if vp.HasField("vehicle") else None,
                "vehicle_label": vp.vehicle.label if vp.HasField("vehicle") else None,
                "license_plate": vp.vehicle.license_plate if vp.HasField("vehicle") else None,
                # Position
                "latitude": vp.position.latitude if vp.HasField("position") else None,
                "longitude": vp.position.longitude if vp.HasField("position") else None,
                "bearing": (
                    vp.position.bearing
                    if vp.HasField("position") and vp.position.HasField("bearing")
                    else None
                ),
                "odometer": (
                    vp.position.odometer
                    if vp.HasField("position") and vp.position.HasField("odometer")
                    else None
                ),
                "speed": (
                    vp.position.speed
                    if vp.HasField("position") and vp.position.HasField("speed")
                    else None
                ),
                # Status
                "current_stop_sequence": (
                    vp.current_stop_sequence if vp.HasField("current_stop_sequence") else None
                ),
                "stop_id": vp.stop_id if vp.HasField("stop_id") else None,
                "current_status": vp.current_status if vp.HasField("current_status") else None,
                "timestamp": vp.timestamp if vp.HasField("timestamp") else None,
                "congestion_level": (
                    vp.congestion_level if vp.HasField("congestion_level") else None
                ),
                "occupancy_status": (
                    vp.occupancy_status if vp.HasField("occupancy_status") else None
                ),
                "occupancy_percentage": (
                    vp.occupancy_percentage if vp.HasField("occupancy_percentage") else None
                ),
            }


def extract_trip_updates(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
) -> Iterator[dict[str, Any]]:
    """Extract trip updates from a FeedMessage (denormalized by stop_time_update)."""
    feed_timestamp = feed.header.timestamp if feed.header.timestamp else None

    for entity in feed.entity:
        if entity.HasField("trip_update"):
            tu = entity.trip_update

            # Base fields for this trip update
            base_record = {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "entity_id": entity.id,
                # Trip descriptor
                "trip_id": tu.trip.trip_id if tu.HasField("trip") else None,
                "route_id": tu.trip.route_id if tu.HasField("trip") else None,
                "direction_id": (
                    tu.trip.direction_id
                    if tu.HasField("trip") and tu.trip.HasField("direction_id")
                    else None
                ),
                "start_time": tu.trip.start_time if tu.HasField("trip") else None,
                "start_date": tu.trip.start_date if tu.HasField("trip") else None,
                "schedule_relationship": (
                    tu.trip.schedule_relationship if tu.HasField("trip") else None
                ),
                # Vehicle descriptor
                "vehicle_id": tu.vehicle.id if tu.HasField("vehicle") else None,
                "vehicle_label": tu.vehicle.label if tu.HasField("vehicle") else None,
                # Trip-level fields
                "trip_timestamp": tu.timestamp if tu.HasField("timestamp") else None,
                "trip_delay": tu.delay if tu.HasField("delay") else None,
            }

            # Denormalize: one row per stop_time_update
            if tu.stop_time_update:
                for stu in tu.stop_time_update:
                    record = base_record.copy()
                    record.update(
                        {
                            "stop_sequence": (
                                stu.stop_sequence if stu.HasField("stop_sequence") else None
                            ),
                            "stop_id": stu.stop_id if stu.HasField("stop_id") else None,
                            "arrival_delay": (
                                stu.arrival.delay
                                if stu.HasField("arrival") and stu.arrival.HasField("delay")
                                else None
                            ),
                            "arrival_time": (
                                stu.arrival.time
                                if stu.HasField("arrival") and stu.arrival.HasField("time")
                                else None
                            ),
                            "arrival_uncertainty": (
                                stu.arrival.uncertainty
                                if stu.HasField("arrival") and stu.arrival.HasField("uncertainty")
                                else None
                            ),
                            "departure_delay": (
                                stu.departure.delay
                                if stu.HasField("departure") and stu.departure.HasField("delay")
                                else None
                            ),
                            "departure_time": (
                                stu.departure.time
                                if stu.HasField("departure") and stu.departure.HasField("time")
                                else None
                            ),
                            "departure_uncertainty": (
                                stu.departure.uncertainty
                                if stu.HasField("departure")
                                and stu.departure.HasField("uncertainty")
                                else None
                            ),
                            "stop_schedule_relationship": (
                                stu.schedule_relationship
                                if stu.HasField("schedule_relationship")
                                else None
                            ),
                        }
                    )
                    yield record
            else:
                # Trip update with no stop time updates - still yield the base record
                record = base_record.copy()
                record.update(
                    {
                        "stop_sequence": None,
                        "stop_id": None,
                        "arrival_delay": None,
                        "arrival_time": None,
                        "arrival_uncertainty": None,
                        "departure_delay": None,
                        "departure_time": None,
                        "departure_uncertainty": None,
                        "stop_schedule_relationship": None,
                    }
                )
                yield record


def extract_service_alerts(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
) -> Iterator[dict[str, Any]]:
    """Extract service alerts from a FeedMessage (denormalized by informed_entity)."""
    feed_timestamp = feed.header.timestamp if feed.header.timestamp else None

    for entity in feed.entity:
        if entity.HasField("alert"):
            alert = entity.alert

            # Get first active period if available
            active_start = None
            active_end = None
            if alert.active_period:
                ap = alert.active_period[0]
                active_start = ap.start if ap.HasField("start") else None
                active_end = ap.end if ap.HasField("end") else None

            # Get first translation for text fields (typically English)
            def get_text(translated_string: Any) -> str | None:
                if translated_string and translated_string.translation:
                    return str(translated_string.translation[0].text)
                return None

            header_text = get_text(alert.header_text) if alert.HasField("header_text") else None
            description_text = (
                get_text(alert.description_text) if alert.HasField("description_text") else None
            )
            url = get_text(alert.url) if alert.HasField("url") else None

            # Base fields for this alert
            base_record = {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "entity_id": entity.id,
                # Alert fields
                "cause": alert.cause if alert.HasField("cause") else None,
                "effect": alert.effect if alert.HasField("effect") else None,
                "severity_level": (
                    alert.severity_level if alert.HasField("severity_level") else None
                ),
                # Active period
                "active_period_start": active_start,
                "active_period_end": active_end,
                # Text fields
                "header_text": header_text,
                "description_text": description_text,
                "url": url,
            }

            # Denormalize: one row per informed_entity
            if alert.informed_entity:
                for ie in alert.informed_entity:
                    record = base_record.copy()
                    record.update(
                        {
                            "agency_id": ie.agency_id if ie.HasField("agency_id") else None,
                            "route_id": ie.route_id if ie.HasField("route_id") else None,
                            "route_type": ie.route_type if ie.HasField("route_type") else None,
                            "stop_id": ie.stop_id if ie.HasField("stop_id") else None,
                            "trip_id": ie.trip.trip_id if ie.HasField("trip") else None,
                            "trip_route_id": ie.trip.route_id if ie.HasField("trip") else None,
                            "trip_direction_id": (
                                ie.trip.direction_id
                                if ie.HasField("trip") and ie.trip.HasField("direction_id")
                                else None
                            ),
                        }
                    )
                    yield record
            else:
                # Alert with no informed entities - still yield the base record
                record = base_record.copy()
                record.update(
                    {
                        "agency_id": None,
                        "route_id": None,
                        "route_type": None,
                        "stop_id": None,
                        "trip_id": None,
                        "trip_route_id": None,
                        "trip_direction_id": None,
                    }
                )
                yield record


def compact_single_feed(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
    feed_type: str,
    schema: pa.Schema,
    extractor: Any,
) -> dg.Output[dict[str, int]]:
    """Compact a single feed for a single date partition.

    Args:
        context: Dagster asset execution context with MultiPartitionKey
        gcs: GCS resource
        feed_type: Feed type (vehicle_positions, trip_updates, service_alerts)
        schema: PyArrow schema for this feed type
        extractor: Function to extract records from protobuf

    Returns:
        Output with metadata about files processed and records written
    """
    # Extract partition dimensions
    partition_keys = context.partition_key.keys_by_dimension
    date = partition_keys["date"]
    feed_key = partition_keys["feed"]  # e.g., "gtfs.example.com/feed" or "~legacy.example.com/feed"

    # Convert partition key to URL and base64url for GCS path lookup
    feed_url = partition_key_to_url(feed_key)
    feed_url_encoded = encode_base64url(feed_url)

    client = gcs.get_client()

    context.log.info(f"Processing {feed_type} for feed={feed_key} on date={date}")

    # List all .pb files for this specific feed and date
    pb_files = list_pb_files(client, gcs.protobuf_bucket, feed_type, date, feed_url_encoded)

    if not pb_files:
        context.log.info(f"No data found for feed {feed_key} on {date}")
        return dg.Output(
            {"files_processed": 0, "records_written": 0},
            metadata={
                "files_processed": 0,
                "records_written": 0,
                "date": date,
                "feed": feed_key,
                "feed_url": feed_url,
            },
        )

    context.log.info(f"Processing {len(pb_files)} files for feed {feed_key}")

    # Stream records to parquet using batched writes to reduce memory usage
    protobuf_bucket = client.bucket(gcs.protobuf_bucket)
    parquet_bucket = client.bucket(gcs.parquet_bucket)

    output_path = f"{feed_type}/date={date}/base64url={feed_url_encoded}/data.parquet"
    buffer = io.BytesIO()
    writer: pq.ParquetWriter | None = None
    records_count = 0

    try:
        for pb_file in pb_files:
            blob = protobuf_bucket.blob(pb_file)
            content = blob.download_as_bytes()

            try:
                feed = parse_protobuf(content)
                records = list(extractor(feed, pb_file, feed_url))
                if not records:
                    continue

                # Write batch to parquet stream
                batch = pa.Table.from_pylist(records, schema=schema)
                if writer is None:
                    writer = pq.ParquetWriter(buffer, schema, compression="snappy")
                writer.write_table(batch)
                records_count += len(records)
            except (DecodeError, ValueError) as e:
                context.log.warning(f"Failed to parse {pb_file}: {e}")
                continue
    finally:
        if writer is not None:
            writer.close()

    if writer is None:
        context.log.info(f"No records extracted for feed {feed_key}")
        return dg.Output(
            {"files_processed": len(pb_files), "records_written": 0},
            metadata={
                "files_processed": len(pb_files),
                "records_written": 0,
                "date": date,
                "feed": feed_key,
                "feed_url": feed_url,
            },
        )

    # Upload parquet file
    buffer.seek(0)

    output_blob = parquet_bucket.blob(output_path)
    output_blob.upload_from_file(buffer, content_type="application/octet-stream")

    context.log.info(f"Wrote {records_count} records to gs://{gcs.parquet_bucket}/{output_path}")

    return dg.Output(
        {"files_processed": len(pb_files), "records_written": records_count},
        metadata={
            "files_processed": len(pb_files),
            "records_written": records_count,
            "date": date,
            "feed": feed_key,
            "feed_url": feed_url,
            "output_path": f"gs://{gcs.parquet_bucket}/{output_path}",
        },
    )


@dg.asset(
    partitions_def=compaction_partitions,
    compute_kind="pyarrow",
    group_name="compaction",
    description="Compacted vehicle positions data in Parquet format",
)
def vehicle_positions_parquet(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
) -> dg.Output[dict[str, int]]:
    """Compact vehicle positions protobuf files into Parquet for a given date and feed."""
    return compact_single_feed(
        context,
        gcs,
        "vehicle_positions",
        VEHICLE_POSITIONS_SCHEMA,
        extract_vehicle_positions,
    )


@dg.asset(
    partitions_def=compaction_partitions,
    compute_kind="pyarrow",
    group_name="compaction",
    description="Compacted trip updates data in Parquet format",
)
def trip_updates_parquet(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
) -> dg.Output[dict[str, int]]:
    """Compact trip updates protobuf files into Parquet for a given date and feed."""
    return compact_single_feed(
        context,
        gcs,
        "trip_updates",
        TRIP_UPDATES_SCHEMA,
        extract_trip_updates,
    )


@dg.asset(
    partitions_def=compaction_partitions,
    compute_kind="pyarrow",
    group_name="compaction",
    description="Compacted service alerts data in Parquet format",
)
def service_alerts_parquet(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
) -> dg.Output[dict[str, int]]:
    """Compact service alerts protobuf files into Parquet for a given date and feed."""
    return compact_single_feed(
        context,
        gcs,
        "service_alerts",
        SERVICE_ALERTS_SCHEMA,
        extract_service_alerts,
    )
