"""Compaction assets for converting protobuf archives to Parquet."""

import base64
import io
import json
import re
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from typing import Any, NamedTuple

import dagster as dg
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage
from google.protobuf.message import DecodeError, Message
from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets.schemas import (
    SERVICE_ALERTS_SCHEMA,
    SHAPES_SCHEMA,
    STOPS_SCHEMA,
    TRIP_MODIFICATIONS_SCHEMA,
    TRIP_UPDATES_SCHEMA,
    VEHICLE_POSITIONS_SCHEMA,
)
from dagster_pipeline.defs.partitions import (
    service_alerts_partitions,
    trip_updates_partitions,
    vehicle_positions_partitions,
)
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

# The (message, field) pairs the extractors dereference that are absent from
# older bindings releases, plus the parent fields of the nested messages they
# read. Deliberately NOT every field the extractors touch: long-stable fields
# (TranslatedString.translation, StopTimeEvent.delay, ...) predate
# every bindings release this code could plausibly meet.
# HasField on a field the installed bindings don't know raises ValueError —
# which the per-file parse handler would swallow, turning a bindings
# downgrade into "every file failed to parse" and successful runs writing
# EMPTY partitions. Assert the whole surface once at import so the code
# server / run worker fails to start instead. tests/dagster iterates this
# same table, so the guard and the extractors cannot drift apart.
# The tuple literal itself dereferences nested message classes
# (TripProperties, ModifiedTripSelector, StopTimeProperties), which raise
# AttributeError on bindings old enough to lack the message — catch that so
# the failure still says "upgrade" instead of a bare AttributeError.
try:
    REQUIRED_BINDINGS_FIELDS: tuple[tuple[type[Message], str], ...] = (
        # FeedHeader.feed_version is a late spec addition (absent from
        # 1.x bindings — verified empirically) and a live HasField target
        # in _header_fields (PR #94 round 14)
        (gtfs_realtime_pb2.FeedHeader, "feed_version"),
        (gtfs_realtime_pb2.VehicleDescriptor, "wheelchair_accessible"),
        # CarriageDetails is 2.1.0-gated; _carriages_json dereferences all
        # five fields (PR #94 round 13)
        (gtfs_realtime_pb2.VehiclePosition, "multi_carriage_details"),
        (gtfs_realtime_pb2.VehiclePosition.CarriageDetails, "id"),
        (gtfs_realtime_pb2.VehiclePosition.CarriageDetails, "label"),
        (gtfs_realtime_pb2.VehiclePosition.CarriageDetails, "occupancy_status"),
        (gtfs_realtime_pb2.VehiclePosition.CarriageDetails, "occupancy_percentage"),
        (gtfs_realtime_pb2.VehiclePosition.CarriageDetails, "carriage_sequence"),
        (gtfs_realtime_pb2.TripUpdate, "trip_properties"),
        (gtfs_realtime_pb2.TripUpdate.TripProperties, "trip_id"),
        (gtfs_realtime_pb2.TripUpdate.TripProperties, "start_date"),
        (gtfs_realtime_pb2.TripUpdate.TripProperties, "start_time"),
        (gtfs_realtime_pb2.TripUpdate.TripProperties, "shape_id"),
        (gtfs_realtime_pb2.TripUpdate.TripProperties, "trip_headsign"),
        (gtfs_realtime_pb2.TripUpdate.TripProperties, "trip_short_name"),
        (gtfs_realtime_pb2.TripDescriptor, "modified_trip"),
        (gtfs_realtime_pb2.TripDescriptor.ModifiedTripSelector, "modifications_id"),
        (gtfs_realtime_pb2.TripDescriptor.ModifiedTripSelector, "affected_trip_id"),
        (gtfs_realtime_pb2.TripDescriptor.ModifiedTripSelector, "start_date"),
        (gtfs_realtime_pb2.TripDescriptor.ModifiedTripSelector, "start_time"),
        (gtfs_realtime_pb2.TripUpdate.StopTimeEvent, "scheduled_time"),
        (gtfs_realtime_pb2.TripUpdate.StopTimeUpdate, "departure_occupancy_status"),
        (gtfs_realtime_pb2.TripUpdate.StopTimeUpdate, "stop_time_properties"),
        (gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties, "assigned_stop_id"),
        (gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties, "stop_headsign"),
        (gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties, "pickup_type"),
        (gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties, "drop_off_type"),
        (gtfs_realtime_pb2.Alert, "cause_detail"),
        (gtfs_realtime_pb2.Alert, "effect_detail"),
        (gtfs_realtime_pb2.Alert, "tts_header_text"),
        (gtfs_realtime_pb2.Alert, "tts_description_text"),
        (gtfs_realtime_pb2.Alert, "communication_period"),
        (gtfs_realtime_pb2.Alert, "impact_period"),
        (gtfs_realtime_pb2.Alert, "image"),
        (gtfs_realtime_pb2.TranslatedImage, "localized_image"),
        (gtfs_realtime_pb2.TranslatedImage.LocalizedImage, "url"),
        (gtfs_realtime_pb2.TranslatedImage.LocalizedImage, "media_type"),
        (gtfs_realtime_pb2.TranslatedImage.LocalizedImage, "language"),
        (gtfs_realtime_pb2.Alert, "image_alternative_text"),
        (gtfs_realtime_pb2.EntitySelector, "direction_id"),
        # Entity-type capture (#95/#96/#97): FeedEntity.shape/stop/
        # trip_modifications are absent from pre-2.2 bindings, so the
        # entity.HasField dispatch itself would raise the swallowed
        # ValueError this guard exists for.
        (gtfs_realtime_pb2.FeedEntity, "shape"),
        (gtfs_realtime_pb2.FeedEntity, "stop"),
        (gtfs_realtime_pb2.FeedEntity, "trip_modifications"),
        (gtfs_realtime_pb2.Shape, "shape_id"),
        (gtfs_realtime_pb2.Shape, "encoded_polyline"),
        (gtfs_realtime_pb2.Stop, "stop_id"),
        (gtfs_realtime_pb2.Stop, "stop_code"),
        (gtfs_realtime_pb2.Stop, "stop_name"),
        (gtfs_realtime_pb2.Stop, "tts_stop_name"),
        (gtfs_realtime_pb2.Stop, "stop_desc"),
        (gtfs_realtime_pb2.Stop, "stop_lat"),
        (gtfs_realtime_pb2.Stop, "stop_lon"),
        (gtfs_realtime_pb2.Stop, "zone_id"),
        (gtfs_realtime_pb2.Stop, "stop_url"),
        (gtfs_realtime_pb2.Stop, "parent_station"),
        (gtfs_realtime_pb2.Stop, "stop_timezone"),
        (gtfs_realtime_pb2.Stop, "wheelchair_boarding"),
        (gtfs_realtime_pb2.Stop, "level_id"),
        (gtfs_realtime_pb2.Stop, "platform_code"),
        (gtfs_realtime_pb2.TripModifications, "selected_trips"),
        (gtfs_realtime_pb2.TripModifications, "start_times"),
        (gtfs_realtime_pb2.TripModifications, "service_dates"),
        (gtfs_realtime_pb2.TripModifications, "modifications"),
        (gtfs_realtime_pb2.TripModifications.SelectedTrips, "trip_ids"),
        (gtfs_realtime_pb2.TripModifications.SelectedTrips, "shape_id"),
        (gtfs_realtime_pb2.TripModifications.Modification, "start_stop_selector"),
        (gtfs_realtime_pb2.TripModifications.Modification, "end_stop_selector"),
        (gtfs_realtime_pb2.TripModifications.Modification, "propagated_modification_delay"),
        (gtfs_realtime_pb2.TripModifications.Modification, "replacement_stops"),
        (gtfs_realtime_pb2.TripModifications.Modification, "service_alert_id"),
        (gtfs_realtime_pb2.TripModifications.Modification, "last_modified_time"),
        (gtfs_realtime_pb2.StopSelector, "stop_sequence"),
        (gtfs_realtime_pb2.StopSelector, "stop_id"),
        (gtfs_realtime_pb2.ReplacementStop, "travel_time_to_stop"),
        (gtfs_realtime_pb2.ReplacementStop, "stop_id"),
    )
except AttributeError as e:
    raise ImportError(
        "gtfs-realtime-bindings is too old for this extraction code; "
        f"missing message type: {e} (need >= 2.2.0)"
    ) from e


def _assert_required_bindings_fields() -> None:
    missing = [
        f"{message.DESCRIPTOR.full_name}.{field}"
        for message, field in REQUIRED_BINDINGS_FIELDS
        if field not in message.DESCRIPTOR.fields_by_name
    ]
    if missing:
        raise ImportError(
            "gtfs-realtime-bindings is too old for this extraction code; "
            f"missing fields: {missing} (need >= 2.2.0)"
        )


_assert_required_bindings_fields()


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


def read_meta_file(
    bucket: storage.Bucket,
    pb_path: str,
) -> datetime | None:
    """Read fetch_timestamp from adjacent .meta file.

    Args:
        bucket: GCS bucket
        pb_path: Path to .pb file

    Returns:
        Parsed datetime or None if .meta file missing/invalid
    """
    meta_path = pb_path.replace(".pb", ".meta")
    try:
        blob = bucket.blob(meta_path)
        content = blob.download_as_text()
        metadata = json.loads(content)
        return datetime.fromisoformat(metadata["fetch_timestamp"])
    except Exception:
        return None


def extract_vehicle_positions(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
    fetch_timestamp: datetime | None,
) -> Iterator[dict[str, Any]]:
    """Extract vehicle positions from a FeedMessage."""
    # Per-field presence like _header_fields on the same message — an
    # explicit timestamp=0 is captured, not NULLed (PR #94 round 14)
    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None
    header_fields = _header_fields(feed)

    for entity in feed.entity:
        if entity.HasField("vehicle"):
            vp = entity.vehicle

            yield {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "fetch_timestamp": fetch_timestamp,
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
                # Per-field presence from v0.9.3 (matching trip_updates —
                # user decision on PR #94): unset plate is NULL, never "".
                # Partitions materialized before v0.9.3 contain "" for a
                # present-descriptor/unset-plate row; see DESIGN.md.
                "license_plate": (
                    vp.vehicle.license_plate if vp.vehicle.HasField("license_plate") else None
                ),
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
                "wheelchair_accessible": (
                    # No parent check needed: an unset submessage returns the
                    # default instance, whose per-field HasField is False.
                    vp.vehicle.wheelchair_accessible
                    if vp.vehicle.HasField("wheelchair_accessible")
                    else None
                ),
                # ModifiedTripSelector — same TripDescriptor field captured
                # in trip_updates, kept symmetric across feed types
                "modified_trip_modifications_id": (
                    vp.trip.modified_trip.modifications_id
                    if vp.trip.modified_trip.HasField("modifications_id")
                    else None
                ),
                "modified_trip_affected_trip_id": (
                    vp.trip.modified_trip.affected_trip_id
                    if vp.trip.modified_trip.HasField("affected_trip_id")
                    else None
                ),
                "modified_trip_start_date": (
                    vp.trip.modified_trip.start_date
                    if vp.trip.modified_trip.HasField("start_date")
                    else None
                ),
                "modified_trip_start_time": (
                    vp.trip.modified_trip.start_time
                    if vp.trip.modified_trip.HasField("start_time")
                    else None
                ),
                # Repeated CarriageDetails, JSON-encoded (unpopulated
                # fleet-wide as of the 2026-08-04 census — day-one capture)
                "multi_carriage_details_json": _carriages_json(vp.multi_carriage_details),
                # Header / entity-level fields (all three feed types)
                **header_fields,
                "is_deleted": entity.is_deleted if entity.HasField("is_deleted") else None,
            }


# The STU-level keys of the denormalized trip_updates row. The no-STU
# fallback branch derives its all-None record from this tuple instead of
# hand-maintaining a duplicate key list; the record↔schema parity test pins
# it against TRIP_UPDATES_SCHEMA.
STOP_TIME_UPDATE_KEYS = (
    "stop_sequence",
    "stop_id",
    "arrival_delay",
    "arrival_time",
    "arrival_uncertainty",
    "departure_delay",
    "departure_time",
    "departure_uncertainty",
    "stop_schedule_relationship",
    "arrival_scheduled_time",
    "departure_scheduled_time",
    "departure_occupancy_status",
    "assigned_stop_id",
    "stop_headsign",
    "pickup_type",
    "drop_off_type",
)

# The informed-entity-level keys of the denormalized service_alerts row
# (EntitySelector semantics — see DESIGN.md); same derivation pattern.
INFORMED_ENTITY_KEYS = (
    "agency_id",
    "route_id",
    "route_type",
    "stop_id",
    "trip_id",
    "trip_route_id",
    "trip_direction_id",
    "direction_id",
    "trip_start_time",
    "trip_start_date",
    "trip_schedule_relationship",
    "trip_modified_trip_modifications_id",
    "trip_modified_trip_affected_trip_id",
    "trip_modified_trip_start_date",
    "trip_modified_trip_start_time",
)


def extract_trip_updates(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
    fetch_timestamp: datetime | None,
) -> Iterator[dict[str, Any]]:
    """Extract trip updates from a FeedMessage (denormalized by stop_time_update)."""
    # Per-field presence like _header_fields on the same message — an
    # explicit timestamp=0 is captured, not NULLed (PR #94 round 14)
    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None
    header_fields = _header_fields(feed)

    for entity in feed.entity:
        if entity.HasField("trip_update"):
            tu = entity.trip_update

            # Base fields for this trip update
            base_record = {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "fetch_timestamp": fetch_timestamp,
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
                # Per-field presence (both feed types, from v0.9.3): unset
                # plate is NULL — plates are rarely published, and parent-only
                # guards would read "" on nearly every row. See DESIGN.md for
                # the pre-v0.9.3 vehicle_positions "" history.
                "license_plate": (
                    tu.vehicle.license_plate if tu.vehicle.HasField("license_plate") else None
                ),
                # Trip-level fields
                "trip_timestamp": tu.timestamp if tu.HasField("timestamp") else None,
                "trip_delay": tu.delay if tu.HasField("delay") else None,
                # TripProperties (added-trip metadata; #91). Per-FIELD presence
                # guards, like the enums: these messages are sparse by design
                # (a producer sets a handful of overrides), and parent-only
                # guards would write "" for every unset sibling — poisoning
                # the IS NOT NULL queries these columns exist for. An unset
                # submessage returns the default instance, whose per-field
                # HasField is False, so no parent check is needed.
                "trip_properties_trip_id": (
                    tu.trip_properties.trip_id if tu.trip_properties.HasField("trip_id") else None
                ),
                "trip_properties_start_date": (
                    tu.trip_properties.start_date
                    if tu.trip_properties.HasField("start_date")
                    else None
                ),
                "trip_properties_start_time": (
                    tu.trip_properties.start_time
                    if tu.trip_properties.HasField("start_time")
                    else None
                ),
                "trip_properties_shape_id": (
                    tu.trip_properties.shape_id if tu.trip_properties.HasField("shape_id") else None
                ),
                "trip_properties_trip_headsign": (
                    tu.trip_properties.trip_headsign
                    if tu.trip_properties.HasField("trip_headsign")
                    else None
                ),
                "trip_properties_trip_short_name": (
                    tu.trip_properties.trip_short_name
                    if tu.trip_properties.HasField("trip_short_name")
                    else None
                ),
                # ModifiedTripSelector (trip-modifications linkage; #91) —
                # same per-field presence discipline
                "modified_trip_modifications_id": (
                    tu.trip.modified_trip.modifications_id
                    if tu.trip.modified_trip.HasField("modifications_id")
                    else None
                ),
                "modified_trip_affected_trip_id": (
                    tu.trip.modified_trip.affected_trip_id
                    if tu.trip.modified_trip.HasField("affected_trip_id")
                    else None
                ),
                "modified_trip_start_date": (
                    tu.trip.modified_trip.start_date
                    if tu.trip.modified_trip.HasField("start_date")
                    else None
                ),
                "modified_trip_start_time": (
                    tu.trip.modified_trip.start_time
                    if tu.trip.modified_trip.HasField("start_time")
                    else None
                ),
                # VehicleDescriptor.wheelchair_accessible — same field
                # captured in vehicle_positions, kept symmetric
                "wheelchair_accessible": (
                    tu.vehicle.wheelchair_accessible
                    if tu.vehicle.HasField("wheelchair_accessible")
                    else None
                ),
                # Header / entity-level fields (all three feed types)
                **header_fields,
                "is_deleted": entity.is_deleted if entity.HasField("is_deleted") else None,
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
                            # bindings >= 2.2.0 fields (#91). No parent
                            # check: an unset submessage returns the default
                            # instance, whose per-field HasField is False.
                            "arrival_scheduled_time": (
                                stu.arrival.scheduled_time
                                if stu.arrival.HasField("scheduled_time")
                                else None
                            ),
                            "departure_scheduled_time": (
                                stu.departure.scheduled_time
                                if stu.departure.HasField("scheduled_time")
                                else None
                            ),
                            "departure_occupancy_status": (
                                stu.departure_occupancy_status
                                if stu.HasField("departure_occupancy_status")
                                else None
                            ),
                            # StopTimeProperties: per-FIELD presence for all
                            # fields (sparse-by-design message; parent-only
                            # guards would write "" for unset strings, and
                            # unset stop_headsign semantically means "inherit
                            # the scheduled headsign", not intentional blank)
                            "assigned_stop_id": (
                                stu.stop_time_properties.assigned_stop_id
                                if stu.stop_time_properties.HasField("assigned_stop_id")
                                else None
                            ),
                            "stop_headsign": (
                                stu.stop_time_properties.stop_headsign
                                if stu.stop_time_properties.HasField("stop_headsign")
                                else None
                            ),
                            "pickup_type": (
                                stu.stop_time_properties.pickup_type
                                if stu.stop_time_properties.HasField("pickup_type")
                                else None
                            ),
                            "drop_off_type": (
                                stu.stop_time_properties.drop_off_type
                                if stu.stop_time_properties.HasField("drop_off_type")
                                else None
                            ),
                        }
                    )
                    yield record
            else:
                # Trip update with no stop time updates - still yield the base record.
                # A base-record key colliding with STOP_TIME_UPDATE_KEYS would
                # be silently NULLed here while every set-based parity check
                # still passes — the static invariant is pinned statically by
                # test_base_record_keys_disjoint_from_child_key_tuples (AST
                # over this dict literal; survives python -O).
                record = base_record.copy()
                record.update(dict.fromkeys(STOP_TIME_UPDATE_KEYS))
                yield record


def _periods_json(periods: Any) -> str | None:
    """JSON-encode a repeated TimeRange ([{start, end}, ...]); NULL when the
    list is empty — deliberately distinct from "[]", which is never emitted.
    Compact separators: the string replicates onto every denormalized row."""
    if not periods:
        return None
    return json.dumps(
        [
            {
                "start": p.start if p.HasField("start") else None,
                "end": p.end if p.HasField("end") else None,
            }
            for p in periods
        ],
        separators=(",", ":"),
    )


def _carriages_json(carriages: Any) -> str | None:
    """JSON-encode repeated CarriageDetails; NULL when the list is empty.
    Per-field presence inside each carriage (unset -> JSON null)."""
    if not carriages:
        return None
    return json.dumps(
        [
            {
                "id": c.id if c.HasField("id") else None,
                "label": c.label if c.HasField("label") else None,
                "occupancy_status": (
                    c.occupancy_status if c.HasField("occupancy_status") else None
                ),
                "occupancy_percentage": (
                    c.occupancy_percentage if c.HasField("occupancy_percentage") else None
                ),
                "carriage_sequence": (
                    c.carriage_sequence if c.HasField("carriage_sequence") else None
                ),
            }
            for c in carriages
        ],
        separators=(",", ":"),
    )


def _header_fields(feed: gtfs_realtime_pb2.FeedMessage) -> dict[str, Any]:
    """Header-level columns shared by all three feed types. incrementality
    uses per-field presence: an explicit FULL_DATASET (0) is captured as 0,
    unset is NULL — and a DIFFERENTIAL publisher becomes visible in data
    (the extractors otherwise assume FULL_DATASET)."""
    header = feed.header
    return {
        "feed_version": header.feed_version if header.HasField("feed_version") else None,
        "incrementality": header.incrementality if header.HasField("incrementality") else None,
    }


def _get_text(translated_string: gtfs_realtime_pb2.TranslatedString) -> str | None:
    """First translation of a TranslatedString (typically English) — the
    keep-first convention documented in DESIGN.md."""
    if translated_string.translation:
        return str(translated_string.translation[0].text)
    return None


def _translations_json(translated_string: gtfs_realtime_pb2.TranslatedString) -> str | None:
    """Full-fidelity TranslatedString capture: [{"text","language"},...];
    NULL when there are no translations, never "[]". Unlike the keep-first
    compat columns, nothing is dropped (#98 option c): every language
    survives in publisher order, so display selection (prefer-en etc.) is
    derivable downstream."""
    if not translated_string.translation:
        return None
    return json.dumps(
        [
            {
                "text": t.text,
                "language": t.language if t.HasField("language") else None,
            }
            for t in translated_string.translation
        ],
        separators=(",", ":"),
    )


def _localized_images_json(localized_images: Any) -> str | None:
    """JSON-encode TranslatedImage.localized_image; NULL when empty.
    url/media_type are proto2-required (always present on a parsed
    message); language is optional (unset -> JSON null)."""
    if not localized_images:
        return None
    return json.dumps(
        [
            {
                "url": li.url,
                "media_type": li.media_type,
                "language": li.language if li.HasField("language") else None,
            }
            for li in localized_images
        ],
        separators=(",", ":"),
    )


def _string_list_json(values: Any) -> str | None:
    """JSON-encode a repeated string field; NULL when empty, never "[]"."""
    if not values:
        return None
    return json.dumps(list(values), separators=(",", ":"))


def _selected_trips_json(selected_trips: Any) -> str | None:
    """JSON-encode repeated TripModifications.SelectedTrips; NULL when empty."""
    if not selected_trips:
        return None
    return json.dumps(
        [
            {
                "trip_ids": list(st.trip_ids),
                "shape_id": st.shape_id if st.HasField("shape_id") else None,
            }
            for st in selected_trips
        ],
        separators=(",", ":"),
    )


def _stop_selector_obj(selector: gtfs_realtime_pb2.StopSelector) -> dict[str, Any]:
    return {
        "stop_sequence": selector.stop_sequence if selector.HasField("stop_sequence") else None,
        "stop_id": selector.stop_id if selector.HasField("stop_id") else None,
    }


def _modifications_json(modifications: Any) -> str | None:
    """JSON-encode repeated TripModifications.Modification; NULL when the
    column-level list is empty. Per-field presence inside (unset -> JSON
    null); nested empty replacement_stops stays [] — the parent exists, its
    list is empty."""
    if not modifications:
        return None
    return json.dumps(
        [
            {
                "start_stop_selector": (
                    _stop_selector_obj(m.start_stop_selector)
                    if m.HasField("start_stop_selector")
                    else None
                ),
                "end_stop_selector": (
                    _stop_selector_obj(m.end_stop_selector)
                    if m.HasField("end_stop_selector")
                    else None
                ),
                "propagated_modification_delay": (
                    m.propagated_modification_delay
                    if m.HasField("propagated_modification_delay")
                    else None
                ),
                "replacement_stops": [
                    {
                        "travel_time_to_stop": (
                            rs.travel_time_to_stop if rs.HasField("travel_time_to_stop") else None
                        ),
                        "stop_id": rs.stop_id if rs.HasField("stop_id") else None,
                    }
                    for rs in m.replacement_stops
                ],
                "service_alert_id": (
                    m.service_alert_id if m.HasField("service_alert_id") else None
                ),
                "last_modified_time": (
                    m.last_modified_time if m.HasField("last_modified_time") else None
                ),
            }
            for m in modifications
        ],
        separators=(",", ":"),
    )


def extract_trip_modifications(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
    fetch_timestamp: datetime | None,
) -> Iterator[dict[str, Any]]:
    """Extract trip_modifications entities (#95). Entity/message grain —
    one row per entity, repeated structures JSON-encoded whole; unnesting
    is a downstream transform concern (DESIGN.md grain-decision entry).
    entity_id is the join target of the trip_updates/vehicle_positions
    modified_trip_modifications_id columns."""
    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None
    header_fields = _header_fields(feed)

    for entity in feed.entity:
        if entity.HasField("trip_modifications"):
            tm = entity.trip_modifications
            yield {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "fetch_timestamp": fetch_timestamp,
                "entity_id": entity.id,
                # TripModifications payload
                "selected_trips_json": _selected_trips_json(tm.selected_trips),
                "start_times_json": _string_list_json(tm.start_times),
                "service_dates_json": _string_list_json(tm.service_dates),
                "modifications_json": _modifications_json(tm.modifications),
                # Header / entity-level fields (all tables)
                **header_fields,
                "is_deleted": entity.is_deleted if entity.HasField("is_deleted") else None,
            }


def extract_shapes(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
    fetch_timestamp: datetime | None,
) -> Iterator[dict[str, Any]]:
    """Extract shape entities (#96) — detour replacement geometry. One row
    per entity per snapshot; identical polylines repeat across a detour's
    lifetime, dedup belongs at query time."""
    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None
    header_fields = _header_fields(feed)

    for entity in feed.entity:
        if entity.HasField("shape"):
            shape = entity.shape
            yield {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "fetch_timestamp": fetch_timestamp,
                "entity_id": entity.id,
                # Shape payload
                "shape_id": shape.shape_id if shape.HasField("shape_id") else None,
                "encoded_polyline": (
                    shape.encoded_polyline if shape.HasField("encoded_polyline") else None
                ),
                # Header / entity-level fields (all tables)
                **header_fields,
                "is_deleted": entity.is_deleted if entity.HasField("is_deleted") else None,
            }


def extract_stops(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
    fetch_timestamp: datetime | None,
) -> Iterator[dict[str, Any]]:
    """Extract stop entities (#97) — ad-hoc/replacement stop definitions
    for detours, possibly absent from static GTFS. Per-field presence for
    scalars; the six TranslatedString fields are captured full-fidelity as
    translations JSON (no keep-first selection baked in — #98)."""
    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None
    header_fields = _header_fields(feed)

    for entity in feed.entity:
        if entity.HasField("stop"):
            stop = entity.stop
            yield {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "fetch_timestamp": fetch_timestamp,
                "entity_id": entity.id,
                # Stop payload (proto field order)
                "stop_id": stop.stop_id if stop.HasField("stop_id") else None,
                "stop_code_translations_json": _translations_json(stop.stop_code),
                "stop_name_translations_json": _translations_json(stop.stop_name),
                "tts_stop_name_translations_json": _translations_json(stop.tts_stop_name),
                "stop_desc_translations_json": _translations_json(stop.stop_desc),
                "stop_lat": stop.stop_lat if stop.HasField("stop_lat") else None,
                "stop_lon": stop.stop_lon if stop.HasField("stop_lon") else None,
                "zone_id": stop.zone_id if stop.HasField("zone_id") else None,
                "stop_url_translations_json": _translations_json(stop.stop_url),
                "parent_station": (
                    stop.parent_station if stop.HasField("parent_station") else None
                ),
                "stop_timezone": stop.stop_timezone if stop.HasField("stop_timezone") else None,
                "wheelchair_boarding": (
                    stop.wheelchair_boarding if stop.HasField("wheelchair_boarding") else None
                ),
                "level_id": stop.level_id if stop.HasField("level_id") else None,
                "platform_code_translations_json": _translations_json(stop.platform_code),
                # Header / entity-level fields (all tables)
                **header_fields,
                "is_deleted": entity.is_deleted if entity.HasField("is_deleted") else None,
            }


def extract_service_alerts(
    feed: gtfs_realtime_pb2.FeedMessage,
    source_file: str,
    feed_url: str,
    fetch_timestamp: datetime | None,
) -> Iterator[dict[str, Any]]:
    """Extract service alerts from a FeedMessage (denormalized by informed_entity)."""
    # Per-field presence like _header_fields on the same message — an
    # explicit timestamp=0 is captured, not NULLed (PR #94 round 14)
    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None
    header_fields = _header_fields(feed)

    for entity in feed.entity:
        if entity.HasField("alert"):
            alert = entity.alert

            # First active period for the compat columns, plus the full list
            # JSON-encoded — 172 fleet alerts carry >1 period (max 251), and
            # keep-first alone misreports "is this alert active at time T"
            # for all of them (#91).
            active_start = None
            active_end = None
            if alert.active_period:
                ap = alert.active_period[0]
                active_start = ap.start if ap.HasField("start") else None
                active_end = ap.end if ap.HasField("end") else None
            active_periods_json = _periods_json(alert.active_period)
            communication_periods_json = _periods_json(alert.communication_period)
            impact_periods_json = _periods_json(alert.impact_period)

            header_text = _get_text(alert.header_text) if alert.HasField("header_text") else None
            description_text = (
                _get_text(alert.description_text) if alert.HasField("description_text") else None
            )
            url = _get_text(alert.url) if alert.HasField("url") else None
            tts_header_text = (
                _get_text(alert.tts_header_text) if alert.HasField("tts_header_text") else None
            )
            tts_description_text = (
                _get_text(alert.tts_description_text)
                if alert.HasField("tts_description_text")
                else None
            )
            cause_detail = _get_text(alert.cause_detail) if alert.HasField("cause_detail") else None
            effect_detail = (
                _get_text(alert.effect_detail) if alert.HasField("effect_detail") else None
            )
            image_url = (
                str(alert.image.localized_image[0].url)
                if alert.HasField("image")
                and alert.image.localized_image
                and alert.image.localized_image[0].HasField("url")
                else None
            )
            image_media_type = (
                str(alert.image.localized_image[0].media_type)
                if alert.HasField("image")
                and alert.image.localized_image
                and alert.image.localized_image[0].HasField("media_type")
                else None
            )
            image_alternative_text = (
                _get_text(alert.image_alternative_text)
                if alert.HasField("image_alternative_text")
                else None
            )

            # Base fields for this alert
            base_record = {
                # Source metadata
                "source_file": source_file,
                "feed_url": feed_url,
                "feed_timestamp": feed_timestamp,
                "fetch_timestamp": fetch_timestamp,
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
                "active_periods_json": active_periods_json,
                # Text fields
                "header_text": header_text,
                "description_text": description_text,
                "url": url,
                "tts_header_text": tts_header_text,
                "tts_description_text": tts_description_text,
                "cause_detail": cause_detail,
                "effect_detail": effect_detail,
                "image_url": image_url,
                "image_media_type": image_media_type,
                "image_alternative_text": image_alternative_text,
                # Communication/impact periods (2.2.0 experimental; unpopulated
                # fleet-wide as of the 2026-08-04 census — day-one capture)
                "communication_periods_json": communication_periods_json,
                "impact_periods_json": impact_periods_json,
                # Full-fidelity translation capture (#98 option c). The
                # keep-first columns above stay as-is — semantics documented
                # as "first translation AS PUBLISHED" (producer whim; AC
                # Transit's first is Spanish). Unset parents fall through:
                # a default-instance TranslatedString has an empty
                # translation list, which the helper maps to NULL.
                "header_text_translations_json": _translations_json(alert.header_text),
                "description_text_translations_json": _translations_json(alert.description_text),
                "url_translations_json": _translations_json(alert.url),
                "tts_header_text_translations_json": _translations_json(alert.tts_header_text),
                "tts_description_text_translations_json": _translations_json(
                    alert.tts_description_text
                ),
                "cause_detail_translations_json": _translations_json(alert.cause_detail),
                "effect_detail_translations_json": _translations_json(alert.effect_detail),
                "image_alternative_text_translations_json": _translations_json(
                    alert.image_alternative_text
                ),
                "image_localized_images_json": _localized_images_json(alert.image.localized_image),
                # Header / entity-level fields (all three feed types)
                **header_fields,
                "is_deleted": entity.is_deleted if entity.HasField("is_deleted") else None,
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
                            "direction_id": (
                                ie.direction_id if ie.HasField("direction_id") else None
                            ),
                            # IE trip descriptor tail (MTA populates start_date
                            # — 2026-08-04 census)
                            "trip_start_time": (
                                ie.trip.start_time
                                if ie.HasField("trip") and ie.trip.HasField("start_time")
                                else None
                            ),
                            "trip_start_date": (
                                ie.trip.start_date
                                if ie.HasField("trip") and ie.trip.HasField("start_date")
                                else None
                            ),
                            "trip_schedule_relationship": (
                                ie.trip.schedule_relationship
                                if ie.HasField("trip") and ie.trip.HasField("schedule_relationship")
                                else None
                            ),
                            "trip_modified_trip_modifications_id": (
                                ie.trip.modified_trip.modifications_id
                                if ie.trip.modified_trip.HasField("modifications_id")
                                else None
                            ),
                            "trip_modified_trip_affected_trip_id": (
                                ie.trip.modified_trip.affected_trip_id
                                if ie.trip.modified_trip.HasField("affected_trip_id")
                                else None
                            ),
                            "trip_modified_trip_start_date": (
                                ie.trip.modified_trip.start_date
                                if ie.trip.modified_trip.HasField("start_date")
                                else None
                            ),
                            "trip_modified_trip_start_time": (
                                ie.trip.modified_trip.start_time
                                if ie.trip.modified_trip.HasField("start_time")
                                else None
                            ),
                        }
                    )
                    yield record
            else:
                # Alert with no informed entities - still yield the base record.
                # A base-record key colliding with INFORMED_ENTITY_KEYS would
                # be silently NULLed here while every set-based parity check
                # still passes — the static invariant is pinned statically by
                # test_base_record_keys_disjoint_from_child_key_tuples (AST
                # over this dict literal; survives python -O).
                record = base_record.copy()
                record.update(dict.fromkeys(INFORMED_ENTITY_KEYS))
                yield record


class TableSpec(NamedTuple):
    """One output table of a compaction pass.

    table_name doubles as the destination prefix in the parquet bucket
    (`{table_name}/date=.../base64url=.../data.parquet`); the READ prefix
    in the protobuf bucket is the feed_type passed to compact_feed_tables —
    the two differ for the tables extracted out of trip_updates raw files.
    """

    table_name: str
    schema: pa.Schema
    extractor: Callable[
        [gtfs_realtime_pb2.FeedMessage, str, str, datetime | None],
        Iterator[dict[str, Any]],
    ]


def compact_feed_tables(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
    feed_type: str,
    tables: Sequence[TableSpec],
) -> dict[str, tuple[dict[str, int], dict[str, Any]]]:
    """Compact one feed's raw .pb files for one date partition into one or
    more Parquet tables, downloading and parsing each file exactly once.

    Args:
        context: Dagster asset execution context with MultiPartitionKey
        gcs: GCS resource
        feed_type: Raw-file prefix in the protobuf bucket
            (vehicle_positions, trip_updates, service_alerts)
        tables: Output tables; each extractor runs against every parsed
            FeedMessage

    Returns:
        {table_name: (output_value, metadata)} — files_processed and
        files_failed are shared across tables (one parse per file),
        records_written and output_path are per table. A table with zero
        records uploads nothing and has no output_path.

    Raises:
        dg.Failure: on Parquet conversion/write failure, naming the
            offending .pb file and table — the partition fails (all
            tables) rather than shipping short. Parse failures
            (DecodeError/ValueError) are per-file: skipped with a warning
            for every table consistently, the rest of the partition still
            writes — UNLESS every file failed, which is systemic and
            fails the partition rather than reporting a green
            zero-record run. The contract is pinned by
            tests/dagster/test_compact_single_feed.py.
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
        return {
            spec.table_name: (
                {"files_processed": 0, "records_written": 0, "files_failed": 0},
                {
                    "files_processed": 0,
                    "records_written": 0,
                    "files_failed": 0,
                    "date": date,
                    "feed": feed_key,
                    "feed_url": feed_url,
                },
            )
            for spec in tables
        }

    context.log.info(f"Processing {len(pb_files)} files for feed {feed_key}")

    # Stream records to parquet using batched writes to reduce memory usage
    protobuf_bucket = client.bucket(gcs.protobuf_bucket)
    parquet_bucket = client.bucket(gcs.parquet_bucket)

    buffers = {spec.table_name: io.BytesIO() for spec in tables}
    writers: dict[str, pq.ParquetWriter | None] = {spec.table_name: None for spec in tables}
    records_counts = {spec.table_name: 0 for spec in tables}
    files_failed = 0
    loop_completed = False

    try:
        for pb_file in pb_files:
            blob = protobuf_bucket.blob(pb_file)
            content = blob.download_as_bytes()

            # Read fetch_timestamp from adjacent .meta file
            fetch_timestamp = read_meta_file(protobuf_bucket, pb_file)

            # The parse-failure handler covers ONLY parsing/extraction: the
            # Arrow conversion sits outside it because pyarrow.ArrowInvalid
            # subclasses ValueError — a schema/type bug caught here would
            # otherwise masquerade as "every file failed to parse" and let a
            # successful run write an empty partition. ALL tables' extraction
            # sits inside it so a failing file is skipped consistently for
            # every output, keeping the tables' row provenance in step.
            try:
                feed = parse_protobuf(content)
                extracted = {
                    spec.table_name: list(spec.extractor(feed, pb_file, feed_url, fetch_timestamp))
                    for spec in tables
                }
            except (DecodeError, ValueError) as e:
                context.log.warning(f"Failed to parse {pb_file}: {e}")
                files_failed += 1
                continue

            # Write batches to the per-table parquet streams. Fail the
            # partition with the offending file and table named — any error
            # escaping here bare gives no clue which of ~thousands of .pb
            # files produced it. Broad on purpose: from_pylist raises
            # ArrowInvalid/ArrowTypeError (which straddle ValueError/
            # TypeError) and bare TypeError/OverflowError depending on the
            # conversion path; the intent is "fail the partition, loudly,
            # naming the file", not enumerating classes.
            for spec in tables:
                records = extracted[spec.table_name]
                if not records:
                    continue
                try:
                    batch = pa.Table.from_pylist(records, schema=spec.schema)
                    writer = writers[spec.table_name]
                    if writer is None:
                        writer = pq.ParquetWriter(
                            buffers[spec.table_name],
                            spec.schema,
                            compression="zstd",
                            compression_level=9,
                        )
                        writers[spec.table_name] = writer
                    writer.write_table(batch)
                except MemoryError:
                    # Not a schema bug — don't send the operator after one.
                    # The identified path is active_periods_json replication
                    # on a worst-case alert (see #92 watch item).
                    raise
                except Exception as e:
                    raise dg.Failure(
                        f"Parquet conversion/write failed for {pb_file} ({spec.table_name}): {e}"
                    ) from e
                records_counts[spec.table_name] += len(records)
        loop_completed = True
    finally:
        # An exception raised in a finally REPLACES the in-flight one — a
        # close() failure after a write_table failure would bury the
        # dg.Failure naming the offending file. Close EVERY writer first
        # (an unclosed ParquetWriter re-raises from __del__ during GC),
        # collecting errors; then swallow them only when the loop did NOT
        # complete (an exception is unwinding). A close failure on the
        # success path must stay loud, or a buffer would be uploaded with a
        # missing/partial footer. (A local flag, not sys.exc_info(): that
        # reflects the whole handler stack, so a caller's except block
        # would wrongly mute a success-path close error.)
        close_errors: list[tuple[str, Exception]] = []
        for table_name, table_writer in writers.items():
            if table_writer is not None:
                try:
                    table_writer.close()
                except Exception as close_err:
                    close_errors.append((table_name, close_err))
        if close_errors:
            if loop_completed:
                for table_name, err in close_errors[1:]:
                    context.log.warning(
                        f"ParquetWriter.close() also failed for {table_name}: {err}"
                    )
                raise close_errors[0][1]
            for table_name, err in close_errors:
                context.log.warning(
                    f"ParquetWriter.close() failed for {table_name} after earlier error: {err}"
                )

    # EVERY file failing to parse is not per-file flakiness — it's systemic
    # (garbage feed content archived all day, or a parser regression the
    # import guard didn't cover) and a green zero-record run would hide it
    # behind metadata nobody is forced to read. Partial failure stays
    # per-file (skip + files_failed); total failure fails the partition.
    # (PR #94 round 13.)
    if files_failed == len(pb_files):
        raise dg.Failure(
            f"All {len(pb_files)} .pb files failed to parse for {feed_type} "
            f"feed {feed_key} on {date} — systemic failure, refusing to "
            f"report a successful zero-record run"
        )

    # Upload per table; a table with zero records uploads nothing (any
    # previously-materialized parquet for the partition is left in place —
    # the existing zero-records convention, applied per output).
    results: dict[str, tuple[dict[str, int], dict[str, Any]]] = {}
    for spec in tables:
        value = {
            "files_processed": len(pb_files),
            "records_written": records_counts[spec.table_name],
            "files_failed": files_failed,
        }
        metadata: dict[str, Any] = {
            **value,
            "date": date,
            "feed": feed_key,
            "feed_url": feed_url,
        }
        if writers[spec.table_name] is None:
            context.log.info(f"No {spec.table_name} records extracted for feed {feed_key}")
        else:
            output_path = f"{spec.table_name}/date={date}/base64url={feed_url_encoded}/data.parquet"
            buffer = buffers[spec.table_name]
            buffer.seek(0)
            output_blob = parquet_bucket.blob(output_path)
            output_blob.upload_from_file(buffer, content_type="application/octet-stream")
            context.log.info(
                f"Wrote {records_counts[spec.table_name]} records "
                f"to gs://{gcs.parquet_bucket}/{output_path}"
            )
            metadata["output_path"] = f"gs://{gcs.parquet_bucket}/{output_path}"
        results[spec.table_name] = (value, metadata)
    return results


def compact_single_feed(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
    feed_type: str,
    schema: pa.Schema,
    extractor: Any,
) -> dg.Output[dict[str, int]]:
    """Compact a single feed into a single table for one date partition —
    a one-table wrapper over compact_feed_tables (which carries the
    error contract)."""
    results = compact_feed_tables(
        context, gcs, feed_type, (TableSpec(feed_type, schema, extractor),)
    )
    value, metadata = results[feed_type]
    return dg.Output(value, metadata=metadata)


@dg.asset(
    partitions_def=vehicle_positions_partitions,
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


# Tables produced from ONE parse of the trip_updates raw files. Madison
# Metro and Big Blue Bus publish trip_modifications/shape/stop entities
# inside their trip_updates feed URLs (2026-08-04 census; #95/#96/#97);
# download + parse are the dominant costs and are already paid, so all four
# tables are extracted in a single pass rather than re-reading ~thousands
# of .pb files per additional table.
TRIP_UPDATES_TABLES: tuple[TableSpec, ...] = (
    TableSpec("trip_updates", TRIP_UPDATES_SCHEMA, extract_trip_updates),
    TableSpec("trip_modifications", TRIP_MODIFICATIONS_SCHEMA, extract_trip_modifications),
    TableSpec("shapes", SHAPES_SCHEMA, extract_shapes),
    TableSpec("stops", STOPS_SCHEMA, extract_stops),
)


@dg.multi_asset(
    partitions_def=trip_updates_partitions,
    compute_kind="pyarrow",
    group_name="compaction",
    outs={
        "trip_updates_parquet": dg.AssetOut(
            description="Compacted trip updates data in Parquet format",
        ),
        "trip_modifications_parquet": dg.AssetOut(
            description=(
                "TripModifications (detour) entities published inside "
                "trip_updates feeds, in Parquet format (#95)"
            ),
        ),
        "shapes_parquet": dg.AssetOut(
            description=(
                "Shape (detour geometry) entities published inside "
                "trip_updates feeds, in Parquet format (#96)"
            ),
        ),
        "stops_parquet": dg.AssetOut(
            description=(
                "Ad-hoc/replacement Stop entities published inside "
                "trip_updates feeds, in Parquet format (#97)"
            ),
        ),
    },
)
def trip_updates_tables(
    context: dg.AssetExecutionContext,
    gcs: GCSResource,
) -> Iterator[dg.Output[dict[str, int]]]:
    """Compact trip updates protobuf files into four Parquet tables for a
    given date and feed — one download/parse pass, one output per table.
    Not subsettable: re-materializing the partition rewrites all four
    outputs (correct — same source bytes)."""
    results = compact_feed_tables(context, gcs, "trip_updates", TRIP_UPDATES_TABLES)
    for spec in TRIP_UPDATES_TABLES:
        value, metadata = results[spec.table_name]
        yield dg.Output(value, output_name=f"{spec.table_name}_parquet", metadata=metadata)


@dg.asset(
    partitions_def=service_alerts_partitions,
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
