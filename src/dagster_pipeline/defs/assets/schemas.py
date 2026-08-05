"""PyArrow schemas for GTFS-RT feed types."""

import pyarrow as pa

# Vehicle Positions Schema
# One row per vehicle position entity in the feed
VEHICLE_POSITIONS_SCHEMA = pa.schema(
    [
        # Source metadata
        pa.field("source_file", pa.string(), nullable=False),
        pa.field("feed_url", pa.string(), nullable=False),
        pa.field("feed_timestamp", pa.uint64()),
        pa.field("fetch_timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("entity_id", pa.string(), nullable=False),
        # Trip descriptor
        pa.field("trip_id", pa.string()),
        pa.field("route_id", pa.string()),
        pa.field("direction_id", pa.uint32()),
        pa.field("start_time", pa.string()),
        pa.field("start_date", pa.string()),
        pa.field("schedule_relationship", pa.int32()),
        # Vehicle descriptor
        pa.field("vehicle_id", pa.string()),
        pa.field("vehicle_label", pa.string()),
        pa.field("license_plate", pa.string()),
        # Position
        pa.field("latitude", pa.float32()),
        pa.field("longitude", pa.float32()),
        pa.field("bearing", pa.float32()),
        pa.field("odometer", pa.float64()),
        pa.field("speed", pa.float32()),
        # Status
        pa.field("current_stop_sequence", pa.uint32()),
        pa.field("stop_id", pa.string()),
        pa.field("current_status", pa.int32()),
        pa.field("timestamp", pa.uint64()),
        pa.field("congestion_level", pa.int32()),
        pa.field("occupancy_status", pa.int32()),
        pa.field("occupancy_percentage", pa.uint32()),
        # Added per #91: unpopulated fleet-wide as of the census, captured
        # from day one (VehicleDescriptor.wheelchair_accessible)
        pa.field("wheelchair_accessible", pa.int32()),
        # ModifiedTripSelector (trip-modifications linkage) — same
        # TripDescriptor field captured in trip_updates; kept symmetric so a
        # vehicle's live position can join to the modification that created
        # its trip (PR #94 review round 7)
        pa.field("modified_trip_modifications_id", pa.string()),
        pa.field("modified_trip_affected_trip_id", pa.string()),
        pa.field("modified_trip_start_date", pa.string()),
        pa.field("modified_trip_start_time", pa.string()),
        # Complete-capture fields (PR #94 round 12 — nothing deferred).
        # Repeated CarriageDetails JSON-encoded; unpopulated fleet-wide
        # (2026-08-04 census)
        pa.field("multi_carriage_details_json", pa.string()),
        # Header / entity-level (all three feed types)
        pa.field("feed_version", pa.string()),
        pa.field("incrementality", pa.int32()),
        pa.field("is_deleted", pa.bool_()),
    ]
)

# Trip Updates Schema
# Denormalized: one row per stop_time_update within each trip update entity
TRIP_UPDATES_SCHEMA = pa.schema(
    [
        # Source metadata
        pa.field("source_file", pa.string(), nullable=False),
        pa.field("feed_url", pa.string(), nullable=False),
        pa.field("feed_timestamp", pa.uint64()),
        pa.field("fetch_timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("entity_id", pa.string(), nullable=False),
        # Trip descriptor
        pa.field("trip_id", pa.string()),
        pa.field("route_id", pa.string()),
        pa.field("direction_id", pa.uint32()),
        pa.field("start_time", pa.string()),
        pa.field("start_date", pa.string()),
        pa.field("schedule_relationship", pa.int32()),
        # Vehicle descriptor
        pa.field("vehicle_id", pa.string()),
        pa.field("vehicle_label", pa.string()),
        # Trip-level fields
        pa.field("trip_timestamp", pa.uint64()),
        pa.field("trip_delay", pa.int32()),
        # Stop time update fields (denormalized)
        pa.field("stop_sequence", pa.uint32()),
        pa.field("stop_id", pa.string()),
        pa.field("arrival_delay", pa.int32()),
        pa.field("arrival_time", pa.int64()),
        pa.field("arrival_uncertainty", pa.int32()),
        pa.field("departure_delay", pa.int32()),
        pa.field("departure_time", pa.int64()),
        pa.field("departure_uncertainty", pa.int32()),
        pa.field("stop_schedule_relationship", pa.int32()),
        # Fields added per the #91 census (scheduled_time additionally
        # requires bindings >= 2.2.0 to be visible at parse time)
        pa.field("license_plate", pa.string()),
        pa.field("arrival_scheduled_time", pa.int64()),
        pa.field("departure_scheduled_time", pa.int64()),
        pa.field("departure_occupancy_status", pa.int32()),
        # StopTimeProperties
        pa.field("assigned_stop_id", pa.string()),
        pa.field("stop_headsign", pa.string()),
        pa.field("pickup_type", pa.int32()),
        pa.field("drop_off_type", pa.int32()),
        # TripProperties (added-trip metadata)
        pa.field("trip_properties_trip_id", pa.string()),
        pa.field("trip_properties_start_date", pa.string()),
        pa.field("trip_properties_start_time", pa.string()),
        pa.field("trip_properties_shape_id", pa.string()),
        pa.field("trip_properties_trip_headsign", pa.string()),
        pa.field("trip_properties_trip_short_name", pa.string()),
        # ModifiedTripSelector (trip-modifications linkage)
        pa.field("modified_trip_modifications_id", pa.string()),
        pa.field("modified_trip_affected_trip_id", pa.string()),
        pa.field("modified_trip_start_date", pa.string()),
        pa.field("modified_trip_start_time", pa.string()),
        # VehicleDescriptor.wheelchair_accessible — same field captured in
        # vehicle_positions; kept symmetric (PR #94 review round 7)
        pa.field("wheelchair_accessible", pa.int32()),
        # Header / entity-level (all three feed types; PR #94 round 12)
        pa.field("feed_version", pa.string()),
        pa.field("incrementality", pa.int32()),
        pa.field("is_deleted", pa.bool_()),
    ]
)

# Service Alerts Schema
# Denormalized: one row per informed_entity within each alert
SERVICE_ALERTS_SCHEMA = pa.schema(
    [
        # Source metadata
        pa.field("source_file", pa.string(), nullable=False),
        pa.field("feed_url", pa.string(), nullable=False),
        pa.field("feed_timestamp", pa.uint64()),
        pa.field("fetch_timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("entity_id", pa.string(), nullable=False),
        # Alert fields
        pa.field("cause", pa.int32()),
        pa.field("effect", pa.int32()),
        pa.field("severity_level", pa.int32()),
        # Active period (first one, or null)
        pa.field("active_period_start", pa.uint64()),
        pa.field("active_period_end", pa.uint64()),
        # Translated text (first translation, typically English)
        pa.field("header_text", pa.string()),
        pa.field("description_text", pa.string()),
        pa.field("url", pa.string()),
        # Informed entity (denormalized - one row per entity)
        pa.field("agency_id", pa.string()),
        pa.field("route_id", pa.string()),
        pa.field("route_type", pa.int32()),
        pa.field("stop_id", pa.string()),
        pa.field("trip_id", pa.string()),
        pa.field("trip_route_id", pa.string()),
        pa.field("trip_direction_id", pa.uint32()),
        pa.field("direction_id", pa.uint32()),
        # Fields added per the #91 census (cause_detail/effect_detail/image
        # require bindings >= 2.2.0). Translated fields keep the
        # first-translation convention.
        pa.field("cause_detail", pa.string()),
        pa.field("effect_detail", pa.string()),
        pa.field("tts_header_text", pa.string()),
        pa.field("tts_description_text", pa.string()),
        pa.field("image_url", pa.string()),
        pa.field("image_media_type", pa.string()),
        pa.field("image_alternative_text", pa.string()),
        # Full active-period list, JSON-encoded [{"start":…,"end":…},…] —
        # 172 fleet alerts carry >1 period (max 251); active_period_start/end
        # remain the first period for compatibility (#91 granularity decision)
        pa.field("active_periods_json", pa.string()),
        # Complete-capture fields (PR #94 round 12 — nothing deferred).
        # communication/impact periods: 2.2.0 experimental, unpopulated
        # fleet-wide (2026-08-04 census); same JSON encoding as active_periods
        pa.field("communication_periods_json", pa.string()),
        pa.field("impact_periods_json", pa.string()),
        # Informed-entity trip descriptor tail (MTA populates trip_start_date)
        pa.field("trip_start_time", pa.string()),
        pa.field("trip_start_date", pa.string()),
        pa.field("trip_schedule_relationship", pa.int32()),
        pa.field("trip_modified_trip_modifications_id", pa.string()),
        pa.field("trip_modified_trip_affected_trip_id", pa.string()),
        pa.field("trip_modified_trip_start_date", pa.string()),
        pa.field("trip_modified_trip_start_time", pa.string()),
        # Header / entity-level (all three feed types)
        pa.field("feed_version", pa.string()),
        pa.field("incrementality", pa.int32()),
        pa.field("is_deleted", pa.bool_()),
    ]
)
