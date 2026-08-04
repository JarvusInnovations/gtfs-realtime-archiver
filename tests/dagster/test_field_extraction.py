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


def test_bindings_expose_required_fields() -> None:
    """Drive the guard from the extractor's own REQUIRED_BINDINGS_FIELDS
    table (which compaction.py also asserts at import, so a bindings
    downgrade fails the code server at startup rather than silently writing
    empty partitions). Iterating the shared table means this test cannot
    drift from what the extractors actually dereference."""
    from dagster_pipeline.defs.assets.compaction import REQUIRED_BINDINGS_FIELDS

    assert len(REQUIRED_BINDINGS_FIELDS) >= 26
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
    assert r["image_alternative_text"] is None  # image set, alt text unset


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
        # Round-trip through Arrow with the explicit schema: catches a
        # wrong-typed value at test time instead of at compaction write time,
        # where it would surface as a per-file warning and a short parquet.
        table = pa.Table.from_pylist(records, schema=schema)
        assert table.num_rows == len(records)


def test_bigquery_ddl_matches_schemas() -> None:
    """Machine-check the schema -> tf/bigquery.tf leg — the one that crosses
    a language boundary with no import to break. A column added to
    schemas.py without a matching BigQuery column ships Parquet data the
    external tables can't see."""
    import re
    from pathlib import Path

    tf_src = (Path(__file__).parents[2] / "tf" / "bigquery.tf").read_text()
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
        entries = re.findall(r'name\s*=\s*"(\w+)",\s*type\s*=\s*"(\w+)"', block.group(1))
        names = [name for name, _t in entries]
        assert names == list(schema.names), (
            f"{table_id}: BigQuery DDL columns diverge from schemas.py"
        )
        # Types matter more than order for a name-matched Parquet external
        # table: a STRING column declared INT64 breaks at query time.
        arrow_to_bq = {
            "string": "STRING",
            "int32": "INT64",
            "int64": "INT64",
            "uint32": "INT64",
            "uint64": "INT64",
            "float": "FLOAT64",
            "double": "FLOAT64",
            "timestamp[us, tz=UTC]": "TIMESTAMP",
        }
        bq_types = dict(entries)
        for field in schema:
            expected = arrow_to_bq[str(field.type)]
            assert bq_types[field.name] == expected, (
                f"{table_id}.{field.name}: BigQuery type {bq_types[field.name]} "
                f"!= expected {expected} for arrow {field.type}"
            )
