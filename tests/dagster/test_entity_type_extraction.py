"""Behavior tests for the entity-type extractors (#95/#96/#97).

Covers: exact JSON encodings at the entity/message grain, NULL (never "[]")
for empty repeated fields, per-field presence inside JSON objects,
full-fidelity multi-language TranslatedString capture (#98), and each
extractor selecting only its own entity type from a mixed feed.
"""

import json

from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets.compaction import (
    extract_service_alerts,
    extract_shapes,
    extract_stops,
    extract_trip_modifications,
    extract_trip_updates,
    extract_vehicle_positions,
)


def _feed() -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_754_000_000
    return feed


def test_trip_modifications_json_encodings_exact() -> None:
    """The four JSON columns must decode to the message's exact structure —
    per-field presence inside objects (unset -> JSON null), nested empty
    replacement_stops as [] (the parent exists, its list is empty)."""
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "tm-1"
    tm = entity.trip_modifications
    st = tm.selected_trips.add()
    st.trip_ids.append("trip-1")
    st.trip_ids.append("trip-2")
    st.shape_id = "shape-detour-1"
    tm.selected_trips.add().trip_ids.append("trip-3")  # no shape_id
    tm.start_times.append("08:00:00")
    tm.start_times.append("08:30:00")
    tm.service_dates.append("20260701")
    m = tm.modifications.add()
    m.start_stop_selector.stop_sequence = 5
    m.end_stop_selector.stop_id = "stop-b"
    m.propagated_modification_delay = 0  # explicit zero must be captured
    m.service_alert_id = "alert-1"
    m.last_modified_time = 1_754_000_060
    rs = m.replacement_stops.add()
    rs.stop_id = "stop-new"  # travel_time_to_stop unset
    tm.modifications.add()  # everything unset

    (record,) = extract_trip_modifications(feed, "f.pb", "u", None)

    assert json.loads(record["selected_trips_json"]) == [
        {"trip_ids": ["trip-1", "trip-2"], "shape_id": "shape-detour-1"},
        {"trip_ids": ["trip-3"], "shape_id": None},
    ]
    assert json.loads(record["start_times_json"]) == ["08:00:00", "08:30:00"]
    assert json.loads(record["service_dates_json"]) == ["20260701"]
    assert json.loads(record["modifications_json"]) == [
        {
            "start_stop_selector": {"stop_sequence": 5, "stop_id": None},
            "end_stop_selector": {"stop_sequence": None, "stop_id": "stop-b"},
            "propagated_modification_delay": 0,
            "replacement_stops": [{"travel_time_to_stop": None, "stop_id": "stop-new"}],
            "service_alert_id": "alert-1",
            "last_modified_time": 1_754_000_060,
        },
        {
            "start_stop_selector": None,
            "end_stop_selector": None,
            "propagated_modification_delay": None,
            "replacement_stops": [],
            "service_alert_id": None,
            "last_modified_time": None,
        },
    ]


def test_trip_modifications_empty_repeated_fields_are_null() -> None:
    """A present-but-empty TripModifications yields NULL in all four JSON
    columns — never "[]"."""
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "tm-empty"
    entity.trip_modifications.SetInParent()

    (record,) = extract_trip_modifications(feed, "f.pb", "u", None)

    assert record["entity_id"] == "tm-empty"
    assert record["selected_trips_json"] is None
    assert record["start_times_json"] is None
    assert record["service_dates_json"] is None
    assert record["modifications_json"] is None


def test_shape_unset_fields_are_null() -> None:
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "shape-empty"
    entity.shape.SetInParent()

    (record,) = extract_shapes(feed, "f.pb", "u", None)

    assert record["entity_id"] == "shape-empty"
    assert record["shape_id"] is None
    assert record["encoded_polyline"] is None


def test_stop_unset_fields_are_null() -> None:
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "stop-empty"
    entity.stop.SetInParent()

    (record,) = extract_stops(feed, "f.pb", "u", None)

    assert record["entity_id"] == "stop-empty"
    payload_keys = set(record) - {
        "source_file",
        "feed_url",
        "feed_timestamp",
        "fetch_timestamp",
        "entity_id",
        "feed_version",
        "incrementality",
        "is_deleted",
    }
    assert payload_keys, "stop record has no payload columns?"
    for key in payload_keys:
        assert record[key] is None, f"{key} should be NULL on an empty Stop"


def test_stop_translations_capture_all_languages() -> None:
    """Full-fidelity TranslatedString capture: every translation survives in
    publisher order with its language tag (missing language -> JSON null) —
    the keep-first loss #98 documents for service_alerts must not exist
    here."""
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "stop-multilang"
    stop = entity.stop
    stop.stop_name.translation.add(text="Parada provisional", language="es")
    stop.stop_name.translation.add(text="Temporary stop", language="en")
    stop.stop_code.translation.add(text="1234")  # no language tag

    (record,) = extract_stops(feed, "f.pb", "u", None)

    assert json.loads(record["stop_name_translations_json"]) == [
        {"text": "Parada provisional", "language": "es"},
        {"text": "Temporary stop", "language": "en"},
    ]
    assert json.loads(record["stop_code_translations_json"]) == [{"text": "1234", "language": None}]
    assert record["tts_stop_name_translations_json"] is None


def test_extractors_select_only_their_entity_type() -> None:
    """One feed carrying all six entity types: each extractor returns
    exactly its own entity — the mixed-entity reality of Madison/BBB
    trip_updates feeds (2026-08-04 census)."""
    feed = _feed()
    e = feed.entity.add()
    e.id = "vp-1"
    e.vehicle.position.latitude = 1.5
    e.vehicle.position.longitude = 2.5
    e = feed.entity.add()
    e.id = "tu-1"
    e.trip_update.trip.trip_id = "trip-1"
    e = feed.entity.add()
    e.id = "sa-1"
    e.alert.header_text.translation.add(text="detour")
    e = feed.entity.add()
    e.id = "tm-1"
    e.trip_modifications.selected_trips.add().trip_ids.append("trip-1")
    e = feed.entity.add()
    e.id = "shape-1"
    e.shape.shape_id = "sh-1"
    e = feed.entity.add()
    e.id = "stop-1"
    e.stop.stop_id = "st-1"

    for extractor, expected_id in (
        (extract_vehicle_positions, "vp-1"),
        (extract_trip_updates, "tu-1"),
        (extract_service_alerts, "sa-1"),
        (extract_trip_modifications, "tm-1"),
        (extract_shapes, "shape-1"),
        (extract_stops, "stop-1"),
    ):
        records = list(extractor(feed, "f.pb", "u", None))
        assert [r["entity_id"] for r in records] == [expected_id], extractor.__name__
