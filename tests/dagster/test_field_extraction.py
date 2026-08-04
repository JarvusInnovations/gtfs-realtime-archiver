"""Tests for the #91 field additions (gtfs-realtime-bindings 2.2.0 fields).

Covers: descriptor visibility (fails loudly on a bindings downgrade), each
newly-extracted field against a synthetic FeedMessage, the multi-active-period
JSON encoding, and record↔schema key parity for all three extractors.
"""

import json

import pyarrow as pa
from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets.compaction import (
    extract_service_alerts,
    extract_trip_updates,
    extract_vehicle_positions,
)
from dagster_pipeline.defs.assets.schemas import (
    SERVICE_ALERTS_SCHEMA,
    TRIP_UPDATES_SCHEMA,
    VEHICLE_POSITIONS_SCHEMA,
)


def test_bindings_expose_2_2_0_fields() -> None:
    """Regression guard: a bindings downgrade below 2.2.0 silently re-blinds
    the parser to these fields; fail loudly instead."""
    stu_event = gtfs_realtime_pb2.TripUpdate.StopTimeEvent.DESCRIPTOR.fields_by_name
    assert "scheduled_time" in stu_event
    alert = gtfs_realtime_pb2.Alert.DESCRIPTOR.fields_by_name
    assert "cause_detail" in alert
    assert "effect_detail" in alert
    assert "image" in alert
    trip = gtfs_realtime_pb2.TripDescriptor.DESCRIPTOR.fields_by_name
    assert "modified_trip" in trip


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


def test_record_keys_match_schemas_exactly() -> None:
    """Every extractor's record keys must equal its schema's column names —
    a missing key writes NULL silently, an extra key is dropped silently;
    both are the drift this test exists to catch."""
    cases: list[tuple[pa.Schema, list[dict]]] = []

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
