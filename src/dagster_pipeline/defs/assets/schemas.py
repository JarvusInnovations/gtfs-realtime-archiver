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
    ]
)
