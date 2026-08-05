"""Tests for the #91 field additions (gtfs-realtime-bindings 2.2.0 fields).

Covers: descriptor visibility (fails loudly on a bindings downgrade), each
newly-extracted field against a synthetic FeedMessage, the multi-active-period
JSON encoding, and record↔schema key parity for all three extractors.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets.compaction import (
    INFORMED_ENTITY_KEYS,
    STOP_TIME_UPDATE_KEYS,
    extract_service_alerts,
    extract_trip_updates,
    extract_vehicle_positions,
)
from dagster_pipeline.defs.assets.schemas import (
    SERVICE_ALERTS_SCHEMA,
    TRIP_UPDATES_SCHEMA,
    VEHICLE_POSITIONS_SCHEMA,
)


def test_bindings_expose_required_fields() -> None:
    """Drive the guard from the extractor's own REQUIRED_BINDINGS_FIELDS
    table (which compaction.py also asserts at import, so a bindings
    downgrade fails the code server at startup rather than silently writing
    empty partitions). Iterating the shared table means this test cannot
    drift from what the extractors actually dereference."""
    from dagster_pipeline.defs.assets.compaction import REQUIRED_BINDINGS_FIELDS

    for message, field in REQUIRED_BINDINGS_FIELDS:
        assert field in message.DESCRIPTOR.fields_by_name, (
            f"{message.DESCRIPTOR.full_name}.{field} missing from installed bindings"
        )


def _feed() -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_754_000_000
    return feed


def test_vehicle_position_wheelchair_accessible() -> None:
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "v1"
    vp = entity.vehicle
    vp.position.latitude = 40.0
    vp.position.longitude = -75.0
    vp.vehicle.id = "bus-1"
    vp.vehicle.wheelchair_accessible = gtfs_realtime_pb2.VehicleDescriptor.WHEELCHAIR_ACCESSIBLE

    (r,) = extract_vehicle_positions(feed, "f.pb", "https://x", None)
    assert r["wheelchair_accessible"] == gtfs_realtime_pb2.VehicleDescriptor.WHEELCHAIR_ACCESSIBLE


def test_wheelchair_accessible_edge_cases() -> None:
    """Absent vehicle descriptor -> NULL; explicit NO_VALUE (enum 0, the
    likeliest wild value) -> 0, not NULL."""
    feed = _feed()
    bare = feed.entity.add()
    bare.id = "v-bare"
    bare.vehicle.position.latitude = 1.0
    bare.vehicle.position.longitude = 2.0
    explicit_zero = feed.entity.add()
    explicit_zero.id = "v-zero"
    explicit_zero.vehicle.position.latitude = 1.0
    explicit_zero.vehicle.position.longitude = 2.0
    explicit_zero.vehicle.vehicle.wheelchair_accessible = (
        gtfs_realtime_pb2.VehicleDescriptor.NO_VALUE
    )

    records = list(extract_vehicle_positions(feed, "f.pb", "https://x", None))
    assert records[0]["wheelchair_accessible"] is None
    assert records[1]["wheelchair_accessible"] == 0


def test_license_plate_null_when_unset_in_both_feed_types() -> None:
    """Pin the migrated convention (DESIGN.md): from v0.9.3 license_plate
    uses per-field presence in BOTH feed types — a vehicle descriptor
    present with no plate reads NULL, never "". (Pre-v0.9.3
    vehicle_positions partitions contain "" — the documented historical
    inconsistency accepted on PR #94.)"""
    feed = _feed()
    vp_entity = feed.entity.add()
    vp_entity.id = "v1"
    vp_entity.vehicle.position.latitude = 1.0
    vp_entity.vehicle.position.longitude = 2.0
    vp_entity.vehicle.vehicle.id = "bus-1"  # descriptor present, plate unset
    tu_entity = feed.entity.add()
    tu_entity.id = "t1"
    tu_entity.trip_update.trip.trip_id = "trip-1"
    tu_entity.trip_update.vehicle.id = "bus-1"  # descriptor present, plate unset

    (vp_record,) = extract_vehicle_positions(feed, "f.pb", "https://x", None)
    (tu_record,) = extract_trip_updates(feed, "f.pb", "https://x", None)
    assert vp_record["license_plate"] is None
    assert tu_record["license_plate"] is None


def test_shared_message_fields_symmetric_across_feed_types() -> None:
    """VehicleDescriptor.wheelchair_accessible and TripDescriptor.
    modified_trip_* are captured in BOTH tables (review round 7): a
    vehicle's live position can join to the trip modification that created
    its trip, and wheelchair data isn't lost when an agency publishes it
    on trip_updates."""
    feed = _feed()
    vp_entity = feed.entity.add()
    vp_entity.id = "v1"
    vp_entity.vehicle.position.latitude = 1.0
    vp_entity.vehicle.position.longitude = 2.0
    vp_entity.vehicle.trip.modified_trip.modifications_id = "mod-7"
    vp_entity.vehicle.trip.modified_trip.affected_trip_id = "orig-9"
    tu_entity = feed.entity.add()
    tu_entity.id = "t1"
    tu_entity.trip_update.trip.trip_id = "trip-1"
    tu_entity.trip_update.vehicle.wheelchair_accessible = (
        gtfs_realtime_pb2.VehicleDescriptor.WHEELCHAIR_ACCESSIBLE
    )

    (vp_record,) = extract_vehicle_positions(feed, "f.pb", "https://x", None)
    (tu_record,) = extract_trip_updates(feed, "f.pb", "https://x", None)
    assert vp_record["modified_trip_modifications_id"] == "mod-7"
    assert vp_record["modified_trip_affected_trip_id"] == "orig-9"
    assert vp_record["modified_trip_start_date"] is None  # per-field presence
    assert (
        tu_record["wheelchair_accessible"]
        == gtfs_realtime_pb2.VehicleDescriptor.WHEELCHAIR_ACCESSIBLE
    )
    # Unset in the opposite table -> NULL
    assert vp_record["wheelchair_accessible"] is None
    assert tu_record["modified_trip_modifications_id"] is None


def test_trip_update_new_fields() -> None:
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "t1"
    tu = entity.trip_update
    tu.trip.trip_id = "trip-1"
    tu.trip.modified_trip.modifications_id = "mod-1"
    tu.trip.modified_trip.affected_trip_id = "orig-trip"
    tu.vehicle.id = "bus-2"
    tu.vehicle.license_plate = "ABC-123"
    tu.trip_properties.trip_id = "added-trip-1"
    tu.trip_properties.shape_id = "shape-9"
    tu.trip_properties.trip_headsign = "Downtown"

    stu = tu.stop_time_update.add()
    stu.stop_id = "stop-1"
    stu.arrival.time = 1_754_000_100
    stu.arrival.scheduled_time = 1_754_000_000
    stu.departure.scheduled_time = 1_754_000_030
    stu.departure_occupancy_status = gtfs_realtime_pb2.VehiclePosition.FULL
    stu.stop_time_properties.assigned_stop_id = "stop-1b"
    stu.stop_time_properties.stop_headsign = "Uptown via Detour"
    stu.stop_time_properties.pickup_type = (
        gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties.COORDINATE_WITH_DRIVER
    )

    (r,) = extract_trip_updates(feed, "f.pb", "https://x", None)
    assert r["license_plate"] == "ABC-123"
    assert r["modified_trip_modifications_id"] == "mod-1"
    assert r["modified_trip_affected_trip_id"] == "orig-trip"
    assert r["trip_properties_trip_id"] == "added-trip-1"
    assert r["trip_properties_shape_id"] == "shape-9"
    assert r["trip_properties_trip_headsign"] == "Downtown"
    assert r["arrival_scheduled_time"] == 1_754_000_000
    assert r["departure_scheduled_time"] == 1_754_000_030
    assert r["departure_occupancy_status"] == gtfs_realtime_pb2.VehiclePosition.FULL
    assert r["assigned_stop_id"] == "stop-1b"
    assert r["stop_headsign"] == "Uptown via Detour"
    assert (
        r["pickup_type"]
        == gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties.COORDINATE_WITH_DRIVER
    )
    assert r["drop_off_type"] is None  # per-field HasField, not enum default
    # Sparse-message strings: unset siblings of set fields are NULL, not ""
    # (trip_properties has trip_id/shape_id/trip_headsign set above)
    assert r["trip_properties_start_date"] is None
    assert r["trip_properties_start_time"] is None
    assert r["trip_properties_trip_short_name"] is None
    assert r["modified_trip_start_date"] is None
    assert r["modified_trip_start_time"] is None


def test_explicit_enum_zero_is_captured_not_nulled() -> None:
    """The sharper half of the HasField semantics: an explicitly-set enum 0
    (REGULAR) must be captured as 0, not confused with unset."""
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "t-zero"
    tu = entity.trip_update
    tu.trip.trip_id = "trip-z"
    stu = tu.stop_time_update.add()
    stu.stop_id = "stop-z"
    stu.stop_time_properties.drop_off_type = (
        gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties.REGULAR
    )

    (r,) = extract_trip_updates(feed, "f.pb", "https://x", None)
    assert r["drop_off_type"] == 0
    assert r["pickup_type"] is None
    # Same shape pins the DESIGN.md string promise: stop_time_properties
    # present (drop_off_type set) with string siblings unset -> NULL, not ""
    assert r["assigned_stop_id"] is None
    assert r["stop_headsign"] is None


def test_service_alert_new_fields_and_multi_active_period() -> None:
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "a1"
    alert = entity.alert

    p1 = alert.active_period.add()
    p1.start, p1.end = 1_754_000_000, 1_754_010_000
    p2 = alert.active_period.add()
    p2.start, p2.end = 1_754_100_000, 1_754_110_000
    p3 = alert.active_period.add()
    p3.start = 1_754_200_000  # open-ended

    alert.header_text.translation.add(text="Detour", language="en")
    alert.tts_header_text.translation.add(text="Dee-tour", language="en")
    alert.tts_description_text.translation.add(text="Spoken details", language="en")
    alert.cause_detail.translation.add(text="Water main break", language="en")
    alert.effect_detail.translation.add(text="Stops 4-9 skipped", language="en")
    alert.image.localized_image.add(url="https://x/img.png", media_type="image/png", language="en")

    ie = alert.informed_entity.add()
    ie.route_id = "route-1"
    ie.direction_id = 1

    (r,) = extract_service_alerts(feed, "f.pb", "https://x", None)
    assert r["tts_header_text"] == "Dee-tour"
    assert r["tts_description_text"] == "Spoken details"
    assert r["cause_detail"] == "Water main break"
    assert r["effect_detail"] == "Stops 4-9 skipped"
    assert r["image_url"] == "https://x/img.png"
    assert r["image_media_type"] == "image/png"
    assert r["direction_id"] == 1
    # compat columns keep the FIRST period
    assert r["active_period_start"] == 1_754_000_000
    assert r["active_period_end"] == 1_754_010_000
    # the JSON column keeps ALL of them, open-ended end as null
    periods = json.loads(r["active_periods_json"])
    assert periods == [
        {"start": 1_754_000_000, "end": 1_754_010_000},
        {"start": 1_754_100_000, "end": 1_754_110_000},
        {"start": 1_754_200_000, "end": None},
    ]
    assert r["image_alternative_text"] is None  # image set, alt text unset


def test_complete_capture_fields() -> None:
    """Round-12 complete-capture columns: header fields, is_deleted,
    carriage JSON, communication/impact period JSON, IE trip tail."""
    feed = _feed()
    feed.header.feed_version = "v42"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    vp_entity = feed.entity.add()
    vp_entity.id = "v1"
    vp_entity.is_deleted = False  # explicit false, like MTA
    vp_entity.vehicle.position.latitude = 1.5
    vp_entity.vehicle.position.longitude = 2.5
    carriage = vp_entity.vehicle.multi_carriage_details.add()
    carriage.label = "Front"  # id/occupancy unset -> JSON nulls
    carriage.carriage_sequence = 1
    sa_entity = feed.entity.add()
    sa_entity.id = "a1"
    alert = sa_entity.alert
    comm = alert.communication_period.add()
    comm.start = 100  # open end
    ie = alert.informed_entity.add()
    ie.trip.trip_id = "trip-9"
    ie.trip.start_date = "20260804"  # the MTA-populated field (census)

    (vp_record, sa_record) = (
        next(extract_vehicle_positions(feed, "f", "u", None)),
        next(extract_service_alerts(feed, "f", "u", None)),
    )
    assert vp_record["feed_version"] == "v42"
    assert vp_record["incrementality"] == 0  # explicit FULL_DATASET, not NULL
    assert vp_record["is_deleted"] is False  # explicit false, not NULL
    assert json.loads(vp_record["multi_carriage_details_json"]) == [
        {
            "id": None,
            "label": "Front",
            "occupancy_status": None,
            "occupancy_percentage": None,
            "carriage_sequence": 1,
        }
    ]
    assert json.loads(sa_record["communication_periods_json"]) == [{"start": 100, "end": None}]
    assert sa_record["impact_periods_json"] is None  # none declared
    assert sa_record["trip_start_date"] == "20260804"
    assert sa_record["trip_start_time"] is None
    assert sa_record["trip_modified_trip_modifications_id"] is None
    # Unset header/entity fields -> NULL, not defaults
    bare_feed = _feed()
    bare = bare_feed.entity.add()
    bare.id = "v-bare"
    bare.vehicle.position.latitude = 1.0
    bare.vehicle.position.longitude = 2.0
    (bare_record,) = extract_vehicle_positions(bare_feed, "f", "u", None)
    assert bare_record["feed_version"] is None
    assert bare_record["incrementality"] is None
    assert bare_record["is_deleted"] is None
    assert bare_record["multi_carriage_details_json"] is None


def test_active_periods_json_single_period() -> None:
    """The overwhelmingly common one-period case emits a one-element JSON
    array — not NULL (that means zero periods) and not a bare object."""
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "a-one"
    period = entity.alert.active_period.add()
    period.start, period.end = 1_754_000_000, 1_754_010_000

    (r,) = extract_service_alerts(feed, "f.pb", "https://x", None)
    assert json.loads(r["active_periods_json"]) == [{"start": 1_754_000_000, "end": 1_754_010_000}]


def test_active_periods_json_null_when_no_periods() -> None:
    """No active periods -> NULL (spec: alert always active), never "[]"."""
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "a-open"
    entity.alert.header_text.translation.add(text="Always on")

    (r,) = extract_service_alerts(feed, "f.pb", "https://x", None)
    assert r["active_periods_json"] is None
    assert r["active_period_start"] is None


def test_record_keys_match_schemas_exactly() -> None:
    """Every extractor's record keys must equal its schema's column names —
    a missing key writes NULL silently, an extra key is dropped silently;
    both are the drift this test exists to catch."""
    cases: list[tuple[pa.Schema, list[dict[str, Any]]]] = []

    vp_feed = _feed()
    e = vp_feed.entity.add()
    e.id = "v"
    e.vehicle.position.latitude = 1.0
    e.vehicle.position.longitude = 2.0
    cases.append(
        (VEHICLE_POSITIONS_SCHEMA, list(extract_vehicle_positions(vp_feed, "f", "u", None)))
    )

    tu_feed = _feed()
    e = tu_feed.entity.add()
    e.id = "t"
    e.trip_update.trip.trip_id = "x"
    e.trip_update.stop_time_update.add().stop_id = "s"
    tu_no_stu_feed = _feed()
    e = tu_no_stu_feed.entity.add()
    e.id = "t2"
    e.trip_update.trip.trip_id = "y"
    cases.append((TRIP_UPDATES_SCHEMA, list(extract_trip_updates(tu_feed, "f", "u", None))))
    cases.append((TRIP_UPDATES_SCHEMA, list(extract_trip_updates(tu_no_stu_feed, "f", "u", None))))

    sa_feed = _feed()
    e = sa_feed.entity.add()
    e.id = "a"
    e.alert.informed_entity.add().route_id = "r"
    sa_no_ie_feed = _feed()
    e = sa_no_ie_feed.entity.add()
    e.id = "a2"
    e.alert.header_text.translation.add(text="h")
    cases.append((SERVICE_ALERTS_SCHEMA, list(extract_service_alerts(sa_feed, "f", "u", None))))
    cases.append(
        (SERVICE_ALERTS_SCHEMA, list(extract_service_alerts(sa_no_ie_feed, "f", "u", None)))
    )

    for schema, records in cases:
        assert records, "each case must yield at least one record"
        for record in records:
            assert set(record.keys()) == set(schema.names)
        # Round-trip through Arrow with the explicit schema: catches a
        # wrong-typed value at test time instead of at compaction write time,
        # where it would surface as a per-file warning and a short parquet.
        table = pa.Table.from_pylist(records, schema=schema)
        assert table.num_rows == len(records)


def _populated_vp_feed() -> gtfs_realtime_pb2.FeedMessage:
    feed = _feed()
    feed.header.feed_version = "producer-v1"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    entity = feed.entity.add()
    entity.id = "v-full"
    entity.is_deleted = False  # explicit, like MTA (census 2026-08-04)
    vp = entity.vehicle
    carriage = vp.multi_carriage_details.add()
    carriage.id = "car-1"
    carriage.label = "Front"
    carriage.occupancy_status = gtfs_realtime_pb2.VehiclePosition.FEW_SEATS_AVAILABLE
    carriage.occupancy_percentage = 55
    carriage.carriage_sequence = 1
    vp.trip.trip_id = "trip-1"
    vp.trip.route_id = "route-1"
    vp.trip.direction_id = 1
    vp.trip.start_time = "08:00:00"
    vp.trip.start_date = "20260701"
    vp.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.SCHEDULED
    vp.trip.modified_trip.modifications_id = "mod-1"
    vp.trip.modified_trip.affected_trip_id = "orig-1"
    vp.trip.modified_trip.start_date = "20260701"
    vp.trip.modified_trip.start_time = "08:00:00"
    vp.vehicle.id = "bus-1"
    vp.vehicle.label = "Bus 1"
    vp.vehicle.license_plate = "PLATE-1"
    vp.vehicle.wheelchair_accessible = gtfs_realtime_pb2.VehicleDescriptor.WHEELCHAIR_ACCESSIBLE
    # Non-integral floats on purpose: pyarrow coerces integral floats into
    # int columns silently, which would hide a float-column-typed-as-int bug
    vp.position.latitude = 40.7
    vp.position.longitude = -75.2
    vp.position.bearing = 90.5
    vp.position.odometer = 12345.6
    vp.position.speed = 8.9
    vp.current_stop_sequence = 3
    vp.stop_id = "stop-3"
    vp.current_status = gtfs_realtime_pb2.VehiclePosition.IN_TRANSIT_TO
    vp.timestamp = 1_754_000_050
    vp.congestion_level = gtfs_realtime_pb2.VehiclePosition.RUNNING_SMOOTHLY
    vp.occupancy_status = gtfs_realtime_pb2.VehiclePosition.FEW_SEATS_AVAILABLE
    vp.occupancy_percentage = 42
    return feed


def _populated_tu_entity(entity: gtfs_realtime_pb2.FeedEntity, with_stu: bool) -> None:
    entity.is_deleted = False
    tu = entity.trip_update
    tu.trip.trip_id = "trip-1"
    tu.trip.route_id = "route-1"
    tu.trip.direction_id = 1
    tu.trip.start_time = "08:00:00"
    tu.trip.start_date = "20260701"
    tu.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.SCHEDULED
    tu.trip.modified_trip.modifications_id = "mod-1"
    tu.trip.modified_trip.affected_trip_id = "orig-1"
    tu.trip.modified_trip.start_date = "20260701"
    tu.trip.modified_trip.start_time = "08:00:00"
    tu.vehicle.id = "bus-1"
    tu.vehicle.label = "Bus 1"
    tu.vehicle.license_plate = "PLATE-1"
    tu.vehicle.wheelchair_accessible = gtfs_realtime_pb2.VehicleDescriptor.WHEELCHAIR_ACCESSIBLE
    tu.timestamp = 1_754_000_050
    tu.delay = 120
    tu.trip_properties.trip_id = "added-1"
    tu.trip_properties.start_date = "20260701"
    tu.trip_properties.start_time = "08:05:00"
    tu.trip_properties.shape_id = "shape-1"
    tu.trip_properties.trip_headsign = "Downtown"
    tu.trip_properties.trip_short_name = "D1"
    if not with_stu:
        return
    stu = tu.stop_time_update.add()
    stu.stop_sequence = 7
    stu.stop_id = "stop-7"
    stu.schedule_relationship = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SCHEDULED
    stu.arrival.delay = 60
    stu.arrival.time = 1_754_000_100
    stu.arrival.uncertainty = 30
    stu.arrival.scheduled_time = 1_754_000_040
    stu.departure.delay = 90
    stu.departure.time = 1_754_000_160
    stu.departure.uncertainty = 45
    stu.departure.scheduled_time = 1_754_000_070
    stu.departure_occupancy_status = gtfs_realtime_pb2.VehiclePosition.FULL
    stu.stop_time_properties.assigned_stop_id = "stop-7b"
    stu.stop_time_properties.stop_headsign = "Uptown"
    stu.stop_time_properties.pickup_type = (
        gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties.PHONE_AGENCY
    )
    stu.stop_time_properties.drop_off_type = (
        gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.StopTimeProperties.COORDINATE_WITH_DRIVER
    )


def _populated_sa_entity(entity: gtfs_realtime_pb2.FeedEntity, with_ie: bool) -> None:
    entity.is_deleted = False
    alert = entity.alert
    period = alert.active_period.add()
    period.start, period.end = 1_754_000_000, 1_754_010_000
    comm = alert.communication_period.add()
    comm.start, comm.end = 1_753_990_000, 1_754_020_000
    impact = alert.impact_period.add()
    impact.start, impact.end = 1_754_000_000, 1_754_005_000
    alert.cause = gtfs_realtime_pb2.Alert.CONSTRUCTION
    alert.effect = gtfs_realtime_pb2.Alert.DETOUR
    alert.severity_level = gtfs_realtime_pb2.Alert.WARNING
    alert.url.translation.add(text="https://x/alert", language="en")
    alert.header_text.translation.add(text="Detour", language="en")
    alert.description_text.translation.add(text="Details", language="en")
    alert.tts_header_text.translation.add(text="Dee-tour", language="en")
    alert.tts_description_text.translation.add(text="Spoken", language="en")
    alert.cause_detail.translation.add(text="Water main", language="en")
    alert.effect_detail.translation.add(text="Stops skipped", language="en")
    alert.image.localized_image.add(url="https://x/i.png", media_type="image/png", language="en")
    alert.image_alternative_text.translation.add(text="Map of detour", language="en")
    if not with_ie:
        return
    ie = alert.informed_entity.add()
    ie.agency_id = "agency-1"
    ie.route_id = "route-1"
    ie.route_type = 3
    ie.stop_id = "stop-1"
    ie.direction_id = 1
    ie.trip.trip_id = "trip-1"
    ie.trip.route_id = "route-1"
    ie.trip.direction_id = 0
    ie.trip.start_time = "08:00:00"
    ie.trip.start_date = "20260701"
    ie.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.SCHEDULED
    ie.trip.modified_trip.modifications_id = "mod-ie"
    ie.trip.modified_trip.affected_trip_id = "orig-ie"
    ie.trip.modified_trip.start_date = "20260701"
    ie.trip.modified_trip.start_time = "08:00:00"


def test_populated_records_have_no_nulls_and_round_trip() -> None:
    """Feed every schema column a real value and round-trip through Arrow.

    The minimal-fixture parity test above proves key parity but its records
    are nearly all NULL — and from_pylist happily writes NULL into ANY Arrow
    type, so it cannot catch a mis-typed column (e.g. active_periods_json
    declared int64). Fully-populated records make the round-trip check
    types for real.

    The fallback-row assertions pin that every populated BASE field survives
    onto no-STU / no-informed-entity rows (a presence-guard bug NULLing one
    would surface as an extra None). A base-record/tuple key collision
    itself is caught by the extractors' runtime disjointness asserts, which
    the fallback entities here exercise."""
    ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)

    vp_records = list(extract_vehicle_positions(_populated_vp_feed(), "f", "u", ts))

    tu_feed = _feed()
    tu_feed.header.feed_version = "producer-v1"
    tu_feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    e = tu_feed.entity.add()
    e.id = "t-full"
    _populated_tu_entity(e, with_stu=True)
    e = tu_feed.entity.add()
    e.id = "t-no-stu"
    _populated_tu_entity(e, with_stu=False)
    tu_records = list(extract_trip_updates(tu_feed, "f", "u", ts))

    sa_feed = _feed()
    sa_feed.header.feed_version = "producer-v1"
    sa_feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    e = sa_feed.entity.add()
    e.id = "a-full"
    _populated_sa_entity(e, with_ie=True)
    e = sa_feed.entity.add()
    e.id = "a-no-ie"
    _populated_sa_entity(e, with_ie=False)
    sa_records = list(extract_service_alerts(sa_feed, "f", "u", ts))

    for table_name, full_record in (
        ("vehicle_positions", vp_records[0]),
        ("trip_updates", tu_records[0]),
        ("service_alerts", sa_records[0]),
    ):
        nones = {k for k, v in full_record.items() if v is None}
        assert not nones, f"{table_name} populated record has NULLs: {nones}"

    # Fallback rows: None for exactly the denormalized child keys, nothing else
    tu_fallback_nones = {k for k, v in tu_records[1].items() if v is None}
    assert tu_fallback_nones == set(STOP_TIME_UPDATE_KEYS)
    sa_fallback_nones = {k for k, v in sa_records[1].items() if v is None}
    assert sa_fallback_nones == set(INFORMED_ENTITY_KEYS)

    # Arrow round-trip with real values in every column — the actual type check
    for schema, records in (
        (VEHICLE_POSITIONS_SCHEMA, vp_records),
        (TRIP_UPDATES_SCHEMA, tu_records),
        (SERVICE_ALERTS_SCHEMA, sa_records),
    ):
        table = pa.Table.from_pylist(records, schema=schema)
        assert table.num_rows == len(records)


def test_bigquery_ddl_matches_schemas() -> None:
    """Machine-check the schema -> tf/bigquery.tf leg — the one that crosses
    a language boundary with no import to break. A column added to
    schemas.py without a matching BigQuery column ships Parquet data the
    external tables can't see.

    Column ORDER is enforced deliberately: BigQuery matches Parquet columns
    by name, so order isn't semantically required — but keeping tf and
    schemas.py as literal mirrors makes review a line-by-line diff. Insert
    mid-list in both files or not at all."""
    tf_path = Path(__file__).parents[2] / "tf" / "bigquery.tf"
    if not tf_path.exists():
        pytest.skip("tf/ not present (e.g. pytest inside the built container)")
    tf_src = tf_path.read_text()
    for table_id, schema in (
        ("vehicle_positions", VEHICLE_POSITIONS_SCHEMA),
        ("trip_updates", TRIP_UPDATES_SCHEMA),
        ("service_alerts", SERVICE_ALERTS_SCHEMA),
    ):
        block = re.search(
            rf'resource "google_bigquery_table" "{table_id}".*?schema = jsonencode\(\[(.*?)\]\)',
            tf_src,
            re.S,
        )
        assert block is not None, f"no schema block for {table_id}"
        entries = re.findall(
            r'name\s*=\s*"(\w+)",\s*type\s*=\s*"(\w+)",\s*mode\s*=\s*"(\w+)"',
            block.group(1),
        )
        # If a tofu fmt reflow breaks the name-type-mode-on-one-line pattern,
        # fail with "parse produced N of M columns", not a confusing name diff.
        assert len(entries) == len(schema.names), (
            f"{table_id}: DDL parse produced {len(entries)} of "
            f"{len(schema.names)} columns — regex no longer matches tf layout?"
        )
        names = [name for name, _t, _m in entries]
        assert names == list(schema.names), (
            f"{table_id}: BigQuery DDL columns diverge from schemas.py"
        )
        # Types and modes matter more than order for a name-matched Parquet
        # external table: a STRING column declared INT64 breaks at query
        # time, and a nullable Arrow field declared REQUIRED breaks on the
        # first NULL row.
        arrow_to_bq = {
            "string": "STRING",
            "int32": "INT64",
            "int64": "INT64",
            "uint32": "INT64",
            "uint64": "INT64",
            "float": "FLOAT64",
            "double": "FLOAT64",
            "timestamp[us, tz=UTC]": "TIMESTAMP",
            "bool": "BOOL",
        }
        bq_entries = {name: (type_, mode) for name, type_, mode in entries}
        for field in schema:
            expected_type = arrow_to_bq[str(field.type)]
            expected_mode = "NULLABLE" if field.nullable else "REQUIRED"
            assert bq_entries[field.name] == (expected_type, expected_mode), (
                f"{table_id}.{field.name}: BigQuery {bq_entries[field.name]} "
                f"!= expected ({expected_type}, {expected_mode}) for arrow "
                f"{field.type} nullable={field.nullable}"
            )
