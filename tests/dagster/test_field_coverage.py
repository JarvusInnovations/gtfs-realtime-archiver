"""Exhaustive GTFS-RT field-coverage manifest (issue #91, PR #94 round 11).

Walks the installed bindings' own descriptor tree from FeedMessage down and
requires every reachable leaf field to have an explicit disposition: either
it feeds one or more Parquet columns, or it is dropped for a recorded
reason. Nothing may be silently unaccounted for.

This is the durable answer to "are we capturing everything?": when a future
gtfs-realtime-bindings bump adds fields, this test FAILS until someone
records a decision for each new field — capture it or document why not.
The reverse direction is also pinned: every schema column must be produced
by at least one proto field (or be pipeline-synthesized), so stale manifest
entries and orphan columns both surface.

Scope: the BASE schema only. Every GTFS-RT message also declares proto2
extension ranges, and producer extensions (e.g. MTA-NYCT's
nyct_subway.proto) arrive as unknown fields a descriptor walk cannot see —
a recorded blind spot (#101), pinned by
test_extension_ranges_are_a_recorded_blind_spot below.
"""

from google.protobuf.descriptor import Descriptor, FieldDescriptor
from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets.schemas import (
    SERVICE_ALERTS_SCHEMA,
    SHAPES_SCHEMA,
    STOPS_SCHEMA,
    TRIP_MODIFICATIONS_SCHEMA,
    TRIP_UPDATES_SCHEMA,
    VEHICLE_POSITIONS_SCHEMA,
)

SCHEMAS = {
    "vehicle_positions": VEHICLE_POSITIONS_SCHEMA,
    "trip_updates": TRIP_UPDATES_SCHEMA,
    "service_alerts": SERVICE_ALERTS_SCHEMA,
    "trip_modifications": TRIP_MODIFICATIONS_SCHEMA,
    "shapes": SHAPES_SCHEMA,
    "stops": STOPS_SCHEMA,
}

# Columns not produced from any proto field: pipeline provenance metadata.
SYNTHESIZED_COLUMNS = {"source_file", "feed_url", "fetch_timestamp"}

# Whole subtrees dropped as a unit; the walk does not descend into them.
# Every entry must name a reason (and an issue where a decision is pending).
# Empty since #95/#96/#97 landed the shape/stop/trip_modifications tables —
# every entity type the bindings know is captured.
DROPPED_SUBTREES: dict[str, str] = {}

# Leaf dispositions: path -> list of "table.column" targets, or a
# "DROP: reason" string. A path may feed multiple columns.
Disposition = list[str] | str

MANIFEST: dict[str, Disposition] = {
    # ---- FeedHeader ----
    "header.gtfs_realtime_version": "DROP: spec version constant, no analytic value",
    "header.incrementality": [
        "vehicle_positions.incrementality",
        "trip_updates.incrementality",
        "service_alerts.incrementality",
        "trip_modifications.incrementality",
        "shapes.incrementality",
        "stops.incrementality",
    ],
    "header.timestamp": [
        "vehicle_positions.feed_timestamp",
        "trip_updates.feed_timestamp",
        "service_alerts.feed_timestamp",
        "trip_modifications.feed_timestamp",
        "shapes.feed_timestamp",
        "stops.feed_timestamp",
    ],
    "header.feed_version": [
        "vehicle_positions.feed_version",
        "trip_updates.feed_version",
        "service_alerts.feed_version",
        "trip_modifications.feed_version",
        "shapes.feed_version",
        "stops.feed_version",
    ],
    # ---- FeedEntity ----
    "entity.id": [
        "vehicle_positions.entity_id",
        "trip_updates.entity_id",
        "service_alerts.entity_id",
        "trip_modifications.entity_id",
        "shapes.entity_id",
        "stops.entity_id",
    ],
    # Captured on payload-bearing entities (MTA sets it explicitly, census
    # 2026-08-04); bare deletion tombstones (id + is_deleted, no payload) are
    # still skipped — that is DIFFERENTIAL row-shape support, and the
    # incrementality column makes any DIFFERENTIAL feed visible in data.
    "entity.is_deleted": [
        "vehicle_positions.is_deleted",
        "trip_updates.is_deleted",
        "service_alerts.is_deleted",
        "trip_modifications.is_deleted",
        "shapes.is_deleted",
        "stops.is_deleted",
    ],
    # ---- VehiclePosition ----
    "entity.vehicle.trip.trip_id": ["vehicle_positions.trip_id"],
    "entity.vehicle.trip.route_id": ["vehicle_positions.route_id"],
    "entity.vehicle.trip.direction_id": ["vehicle_positions.direction_id"],
    "entity.vehicle.trip.start_time": ["vehicle_positions.start_time"],
    "entity.vehicle.trip.start_date": ["vehicle_positions.start_date"],
    "entity.vehicle.trip.schedule_relationship": ["vehicle_positions.schedule_relationship"],
    "entity.vehicle.trip.modified_trip.modifications_id": [
        "vehicle_positions.modified_trip_modifications_id"
    ],
    "entity.vehicle.trip.modified_trip.affected_trip_id": [
        "vehicle_positions.modified_trip_affected_trip_id"
    ],
    "entity.vehicle.trip.modified_trip.start_time": ["vehicle_positions.modified_trip_start_time"],
    "entity.vehicle.trip.modified_trip.start_date": ["vehicle_positions.modified_trip_start_date"],
    "entity.vehicle.vehicle.id": ["vehicle_positions.vehicle_id"],
    "entity.vehicle.vehicle.label": ["vehicle_positions.vehicle_label"],
    "entity.vehicle.vehicle.license_plate": ["vehicle_positions.license_plate"],
    "entity.vehicle.vehicle.wheelchair_accessible": ["vehicle_positions.wheelchair_accessible"],
    "entity.vehicle.position.latitude": ["vehicle_positions.latitude"],
    "entity.vehicle.position.longitude": ["vehicle_positions.longitude"],
    "entity.vehicle.position.bearing": ["vehicle_positions.bearing"],
    "entity.vehicle.position.odometer": ["vehicle_positions.odometer"],
    "entity.vehicle.position.speed": ["vehicle_positions.speed"],
    "entity.vehicle.current_stop_sequence": ["vehicle_positions.current_stop_sequence"],
    "entity.vehicle.stop_id": ["vehicle_positions.stop_id"],
    "entity.vehicle.current_status": ["vehicle_positions.current_status"],
    "entity.vehicle.timestamp": ["vehicle_positions.timestamp"],
    "entity.vehicle.congestion_level": ["vehicle_positions.congestion_level"],
    "entity.vehicle.occupancy_status": ["vehicle_positions.occupancy_status"],
    "entity.vehicle.occupancy_percentage": ["vehicle_positions.occupancy_percentage"],
    "entity.vehicle.multi_carriage_details.id": ["vehicle_positions.multi_carriage_details_json"],
    "entity.vehicle.multi_carriage_details.label": [
        "vehicle_positions.multi_carriage_details_json"
    ],
    "entity.vehicle.multi_carriage_details.occupancy_status": [
        "vehicle_positions.multi_carriage_details_json"
    ],
    "entity.vehicle.multi_carriage_details.occupancy_percentage": [
        "vehicle_positions.multi_carriage_details_json"
    ],
    "entity.vehicle.multi_carriage_details.carriage_sequence": [
        "vehicle_positions.multi_carriage_details_json"
    ],
    # ---- TripUpdate ----
    "entity.trip_update.trip.trip_id": ["trip_updates.trip_id"],
    "entity.trip_update.trip.route_id": ["trip_updates.route_id"],
    "entity.trip_update.trip.direction_id": ["trip_updates.direction_id"],
    "entity.trip_update.trip.start_time": ["trip_updates.start_time"],
    "entity.trip_update.trip.start_date": ["trip_updates.start_date"],
    "entity.trip_update.trip.schedule_relationship": ["trip_updates.schedule_relationship"],
    "entity.trip_update.trip.modified_trip.modifications_id": [
        "trip_updates.modified_trip_modifications_id"
    ],
    "entity.trip_update.trip.modified_trip.affected_trip_id": [
        "trip_updates.modified_trip_affected_trip_id"
    ],
    "entity.trip_update.trip.modified_trip.start_time": ["trip_updates.modified_trip_start_time"],
    "entity.trip_update.trip.modified_trip.start_date": ["trip_updates.modified_trip_start_date"],
    "entity.trip_update.vehicle.id": ["trip_updates.vehicle_id"],
    "entity.trip_update.vehicle.label": ["trip_updates.vehicle_label"],
    "entity.trip_update.vehicle.license_plate": ["trip_updates.license_plate"],
    "entity.trip_update.vehicle.wheelchair_accessible": ["trip_updates.wheelchair_accessible"],
    "entity.trip_update.stop_time_update.stop_sequence": ["trip_updates.stop_sequence"],
    "entity.trip_update.stop_time_update.stop_id": ["trip_updates.stop_id"],
    "entity.trip_update.stop_time_update.arrival.delay": ["trip_updates.arrival_delay"],
    "entity.trip_update.stop_time_update.arrival.time": ["trip_updates.arrival_time"],
    "entity.trip_update.stop_time_update.arrival.uncertainty": ["trip_updates.arrival_uncertainty"],
    "entity.trip_update.stop_time_update.arrival.scheduled_time": [
        "trip_updates.arrival_scheduled_time"
    ],
    "entity.trip_update.stop_time_update.departure.delay": ["trip_updates.departure_delay"],
    "entity.trip_update.stop_time_update.departure.time": ["trip_updates.departure_time"],
    "entity.trip_update.stop_time_update.departure.uncertainty": [
        "trip_updates.departure_uncertainty"
    ],
    "entity.trip_update.stop_time_update.departure.scheduled_time": [
        "trip_updates.departure_scheduled_time"
    ],
    "entity.trip_update.stop_time_update.departure_occupancy_status": [
        "trip_updates.departure_occupancy_status"
    ],
    "entity.trip_update.stop_time_update.schedule_relationship": [
        "trip_updates.stop_schedule_relationship"
    ],
    "entity.trip_update.stop_time_update.stop_time_properties.assigned_stop_id": [
        "trip_updates.assigned_stop_id"
    ],
    "entity.trip_update.stop_time_update.stop_time_properties.stop_headsign": [
        "trip_updates.stop_headsign"
    ],
    "entity.trip_update.stop_time_update.stop_time_properties.pickup_type": [
        "trip_updates.pickup_type"
    ],
    "entity.trip_update.stop_time_update.stop_time_properties.drop_off_type": [
        "trip_updates.drop_off_type"
    ],
    "entity.trip_update.timestamp": ["trip_updates.trip_timestamp"],
    "entity.trip_update.delay": ["trip_updates.trip_delay"],
    "entity.trip_update.trip_properties.trip_id": ["trip_updates.trip_properties_trip_id"],
    "entity.trip_update.trip_properties.start_date": ["trip_updates.trip_properties_start_date"],
    "entity.trip_update.trip_properties.start_time": ["trip_updates.trip_properties_start_time"],
    "entity.trip_update.trip_properties.shape_id": ["trip_updates.trip_properties_shape_id"],
    "entity.trip_update.trip_properties.trip_headsign": [
        "trip_updates.trip_properties_trip_headsign"
    ],
    "entity.trip_update.trip_properties.trip_short_name": [
        "trip_updates.trip_properties_trip_short_name"
    ],
    # ---- Alert ----
    "entity.alert.active_period.start": [
        "service_alerts.active_period_start",
        "service_alerts.active_periods_json",
    ],
    "entity.alert.active_period.end": [
        "service_alerts.active_period_end",
        "service_alerts.active_periods_json",
    ],
    "entity.alert.communication_period.start": ["service_alerts.communication_periods_json"],
    "entity.alert.communication_period.end": ["service_alerts.communication_periods_json"],
    "entity.alert.impact_period.start": ["service_alerts.impact_periods_json"],
    "entity.alert.impact_period.end": ["service_alerts.impact_periods_json"],
    "entity.alert.informed_entity.agency_id": ["service_alerts.agency_id"],
    "entity.alert.informed_entity.route_id": ["service_alerts.route_id"],
    "entity.alert.informed_entity.route_type": ["service_alerts.route_type"],
    "entity.alert.informed_entity.stop_id": ["service_alerts.stop_id"],
    "entity.alert.informed_entity.direction_id": ["service_alerts.direction_id"],
    "entity.alert.informed_entity.trip.trip_id": ["service_alerts.trip_id"],
    "entity.alert.informed_entity.trip.route_id": ["service_alerts.trip_route_id"],
    "entity.alert.informed_entity.trip.direction_id": ["service_alerts.trip_direction_id"],
    "entity.alert.informed_entity.trip.start_time": ["service_alerts.trip_start_time"],
    "entity.alert.informed_entity.trip.start_date": ["service_alerts.trip_start_date"],
    "entity.alert.informed_entity.trip.schedule_relationship": [
        "service_alerts.trip_schedule_relationship"
    ],
    "entity.alert.informed_entity.trip.modified_trip.modifications_id": [
        "service_alerts.trip_modified_trip_modifications_id"
    ],
    "entity.alert.informed_entity.trip.modified_trip.affected_trip_id": [
        "service_alerts.trip_modified_trip_affected_trip_id"
    ],
    "entity.alert.informed_entity.trip.modified_trip.start_date": [
        "service_alerts.trip_modified_trip_start_date"
    ],
    "entity.alert.informed_entity.trip.modified_trip.start_time": [
        "service_alerts.trip_modified_trip_start_time"
    ],
    "entity.alert.cause": ["service_alerts.cause"],
    "entity.alert.effect": ["service_alerts.effect"],
    "entity.alert.severity_level": ["service_alerts.severity_level"],
    # Translated fields (#98 option c): .text feeds the keep-first compat
    # column AND the full-fidelity translations JSON; .language is captured
    # in the JSON (the pre-#98 keep-first DROPs are gone).
    "entity.alert.url.translation.text": [
        "service_alerts.url",
        "service_alerts.url_translations_json",
    ],
    "entity.alert.url.translation.language": ["service_alerts.url_translations_json"],
    "entity.alert.header_text.translation.text": [
        "service_alerts.header_text",
        "service_alerts.header_text_translations_json",
    ],
    "entity.alert.header_text.translation.language": [
        "service_alerts.header_text_translations_json"
    ],
    "entity.alert.description_text.translation.text": [
        "service_alerts.description_text",
        "service_alerts.description_text_translations_json",
    ],
    "entity.alert.description_text.translation.language": [
        "service_alerts.description_text_translations_json"
    ],
    "entity.alert.tts_header_text.translation.text": [
        "service_alerts.tts_header_text",
        "service_alerts.tts_header_text_translations_json",
    ],
    "entity.alert.tts_header_text.translation.language": [
        "service_alerts.tts_header_text_translations_json"
    ],
    "entity.alert.tts_description_text.translation.text": [
        "service_alerts.tts_description_text",
        "service_alerts.tts_description_text_translations_json",
    ],
    "entity.alert.tts_description_text.translation.language": [
        "service_alerts.tts_description_text_translations_json"
    ],
    "entity.alert.cause_detail.translation.text": [
        "service_alerts.cause_detail",
        "service_alerts.cause_detail_translations_json",
    ],
    "entity.alert.cause_detail.translation.language": [
        "service_alerts.cause_detail_translations_json"
    ],
    "entity.alert.effect_detail.translation.text": [
        "service_alerts.effect_detail",
        "service_alerts.effect_detail_translations_json",
    ],
    "entity.alert.effect_detail.translation.language": [
        "service_alerts.effect_detail_translations_json"
    ],
    "entity.alert.image.localized_image.url": [
        "service_alerts.image_url",
        "service_alerts.image_localized_images_json",
    ],
    "entity.alert.image.localized_image.media_type": [
        "service_alerts.image_media_type",
        "service_alerts.image_localized_images_json",
    ],
    "entity.alert.image.localized_image.language": ["service_alerts.image_localized_images_json"],
    "entity.alert.image_alternative_text.translation.text": [
        "service_alerts.image_alternative_text",
        "service_alerts.image_alternative_text_translations_json",
    ],
    "entity.alert.image_alternative_text.translation.language": [
        "service_alerts.image_alternative_text_translations_json"
    ],
    # ---- TripModifications (#95) ----
    # Entity/message grain: repeated structures JSON-encoded whole
    "entity.trip_modifications.selected_trips.trip_ids": ["trip_modifications.selected_trips_json"],
    "entity.trip_modifications.selected_trips.shape_id": ["trip_modifications.selected_trips_json"],
    "entity.trip_modifications.start_times": ["trip_modifications.start_times_json"],
    "entity.trip_modifications.service_dates": ["trip_modifications.service_dates_json"],
    "entity.trip_modifications.modifications.start_stop_selector.stop_sequence": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.start_stop_selector.stop_id": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.end_stop_selector.stop_sequence": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.end_stop_selector.stop_id": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.propagated_modification_delay": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.replacement_stops.travel_time_to_stop": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.replacement_stops.stop_id": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.service_alert_id": [
        "trip_modifications.modifications_json"
    ],
    "entity.trip_modifications.modifications.last_modified_time": [
        "trip_modifications.modifications_json"
    ],
    # ---- Shape (#96) ----
    "entity.shape.shape_id": ["shapes.shape_id"],
    "entity.shape.encoded_polyline": ["shapes.encoded_polyline"],
    # ---- Stop (#97) ----
    # TranslatedStrings captured full-fidelity as [{"text","language"},...]
    # JSON — including .language, unlike the service_alerts keep-first drops
    "entity.stop.stop_id": ["stops.stop_id"],
    "entity.stop.stop_code.translation.text": ["stops.stop_code_translations_json"],
    "entity.stop.stop_code.translation.language": ["stops.stop_code_translations_json"],
    "entity.stop.stop_name.translation.text": ["stops.stop_name_translations_json"],
    "entity.stop.stop_name.translation.language": ["stops.stop_name_translations_json"],
    "entity.stop.tts_stop_name.translation.text": ["stops.tts_stop_name_translations_json"],
    "entity.stop.tts_stop_name.translation.language": ["stops.tts_stop_name_translations_json"],
    "entity.stop.stop_desc.translation.text": ["stops.stop_desc_translations_json"],
    "entity.stop.stop_desc.translation.language": ["stops.stop_desc_translations_json"],
    "entity.stop.stop_lat": ["stops.stop_lat"],
    "entity.stop.stop_lon": ["stops.stop_lon"],
    "entity.stop.zone_id": ["stops.zone_id"],
    "entity.stop.stop_url.translation.text": ["stops.stop_url_translations_json"],
    "entity.stop.stop_url.translation.language": ["stops.stop_url_translations_json"],
    "entity.stop.parent_station": ["stops.parent_station"],
    "entity.stop.stop_timezone": ["stops.stop_timezone"],
    "entity.stop.wheelchair_boarding": ["stops.wheelchair_boarding"],
    "entity.stop.level_id": ["stops.level_id"],
    "entity.stop.platform_code.translation.text": ["stops.platform_code_translations_json"],
    "entity.stop.platform_code.translation.language": ["stops.platform_code_translations_json"],
}


def _walk_leaves(
    desc: Descriptor, prefix: str, ancestors: frozenset[str] = frozenset()
) -> list[str]:
    ancestors = ancestors | {desc.full_name}
    leaves: list[str] = []
    for field in desc.fields:
        path = f"{prefix}.{field.name}" if prefix else field.name
        if path in DROPPED_SUBTREES:
            continue
        if field.type == FieldDescriptor.TYPE_MESSAGE:
            # A self/mutually-recursive message type would recurse forever,
            # turning "fails until dispositioned" into "hangs CI" — the
            # worst failure mode for a test whose job is to fail loudly.
            # Acyclic today; assert so a future bindings release breaks
            # loudly instead.
            assert field.message_type.full_name not in ancestors, (
                f"recursive message type {field.message_type.full_name} at {path}; "
                "record it as a DROPPED_SUBTREES entry with a capture decision"
            )
            leaves.extend(_walk_leaves(field.message_type, path, ancestors))
        else:
            leaves.append(path)
    return leaves


def test_every_reachable_proto_field_has_a_disposition() -> None:
    """A bindings bump that adds fields fails here until each new field is
    either captured or given a recorded DROP reason."""
    leaves = _walk_leaves(gtfs_realtime_pb2.FeedMessage.DESCRIPTOR, "")
    unaccounted = [leaf for leaf in leaves if leaf not in MANIFEST]
    assert not unaccounted, f"proto fields with no recorded capture/drop decision: {unaccounted}"


def test_manifest_has_no_stale_entries() -> None:
    """Manifest paths and dropped-subtree prefixes must exist in the
    installed bindings — a renamed or removed field surfaces here."""
    leaves = set(_walk_leaves(gtfs_realtime_pb2.FeedMessage.DESCRIPTOR, ""))
    stale = [path for path in MANIFEST if path not in leaves]
    assert not stale, f"manifest entries for nonexistent proto fields: {stale}"

    def _prefix_exists(prefix: str) -> bool:
        desc: Descriptor = gtfs_realtime_pb2.FeedMessage.DESCRIPTOR
        for part in prefix.split("."):
            field = desc.fields_by_name.get(part)
            if field is None or field.type != FieldDescriptor.TYPE_MESSAGE:
                return False
            desc = field.message_type
        return True

    stale_subtrees = [p for p in DROPPED_SUBTREES if not _prefix_exists(p)]
    assert not stale_subtrees, f"dropped-subtree prefixes not in bindings: {stale_subtrees}"


def test_extension_ranges_are_a_recorded_blind_spot() -> None:
    """The walk iterates desc.fields, which by construction cannot see
    proto2 extension ranges — producer extension payloads (MTA-NYCT et al.)
    land in unknown fields and are dropped at compaction, surviving only in
    the raw .pb archive (#101). Pin that this blind spot applies to every
    reachable message, so the manifest's "nothing unaccounted for" claim is
    honestly scoped to the base schema; if a bindings release ever removes
    the extension ranges, this fails and the caveat can be retired."""

    def _messages(desc: Descriptor, seen: dict[str, Descriptor]) -> None:
        if desc.full_name in seen:
            return
        seen[desc.full_name] = desc
        for field in desc.fields:
            if field.type == FieldDescriptor.TYPE_MESSAGE:
                _messages(field.message_type, seen)

    seen: dict[str, Descriptor] = {}
    _messages(gtfs_realtime_pb2.FeedMessage.DESCRIPTOR, seen)
    assert len(seen) > 10, "message walk found suspiciously few types"
    without_ranges = sorted(n for n, d in seen.items() if not d.extension_ranges)
    assert not without_ranges, (
        f"messages without extension ranges appeared: {without_ranges} — "
        "revisit the #101 blind-spot caveat"
    )


def test_manifest_columns_exist_and_cover_schemas() -> None:
    """Every capture target must be a real schema column, and every schema
    column must be fed by at least one proto field or be synthesized."""
    covered: dict[str, set[str]] = {name: set() for name in SCHEMAS}
    for path, disposition in MANIFEST.items():
        if isinstance(disposition, str):
            assert disposition.startswith("DROP: "), f"{path}: bad disposition"
            continue
        for target in disposition:
            table, column = target.split(".", 1)
            assert column in SCHEMAS[table].names, f"{path} -> {target}: no such column"
            covered[table].add(column)

    for table, schema in SCHEMAS.items():
        orphans = set(schema.names) - covered[table] - SYNTHESIZED_COLUMNS
        assert not orphans, f"{table} columns fed by no proto field: {orphans}"
