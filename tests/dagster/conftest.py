"""Fixtures for Dagster pipeline tests."""

import pytest
from google.transit import gtfs_realtime_pb2


@pytest.fixture
def sample_vehicle_position_feed() -> gtfs_realtime_pb2.FeedMessage:
    """Create a sample FeedMessage with vehicle positions."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1704067200  # 2024-01-01 00:00:00 UTC

    # Entity with full vehicle position data
    entity1 = feed.entity.add()
    entity1.id = "vehicle-1"
    entity1.vehicle.trip.trip_id = "trip-123"
    entity1.vehicle.trip.route_id = "route-A"
    entity1.vehicle.trip.direction_id = 0
    entity1.vehicle.trip.start_time = "08:00:00"
    entity1.vehicle.trip.start_date = "20240101"
    entity1.vehicle.vehicle.id = "bus-001"
    entity1.vehicle.vehicle.label = "Bus 1"
    entity1.vehicle.position.latitude = 39.9526
    entity1.vehicle.position.longitude = -75.1652
    entity1.vehicle.position.bearing = 180.0
    entity1.vehicle.position.speed = 12.5
    entity1.vehicle.current_stop_sequence = 5
    entity1.vehicle.stop_id = "stop-100"
    entity1.vehicle.current_status = gtfs_realtime_pb2.VehiclePosition.INCOMING_AT
    entity1.vehicle.timestamp = 1704067200
    entity1.vehicle.congestion_level = gtfs_realtime_pb2.VehiclePosition.RUNNING_SMOOTHLY
    entity1.vehicle.occupancy_status = gtfs_realtime_pb2.VehiclePosition.MANY_SEATS_AVAILABLE

    # Entity with minimal data (tests optional field handling)
    entity2 = feed.entity.add()
    entity2.id = "vehicle-2"
    entity2.vehicle.position.latitude = 40.0
    entity2.vehicle.position.longitude = -75.0

    return feed


@pytest.fixture
def sample_trip_update_feed() -> gtfs_realtime_pb2.FeedMessage:
    """Create a sample FeedMessage with trip updates."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1704067200

    # Entity with stop time updates
    entity1 = feed.entity.add()
    entity1.id = "trip-update-1"
    entity1.trip_update.trip.trip_id = "trip-456"
    entity1.trip_update.trip.route_id = "route-B"
    entity1.trip_update.trip.start_date = "20240101"
    entity1.trip_update.vehicle.id = "bus-002"
    entity1.trip_update.timestamp = 1704067200
    entity1.trip_update.delay = 120  # 2 minutes late

    # Add stop time updates
    stu1 = entity1.trip_update.stop_time_update.add()
    stu1.stop_sequence = 1
    stu1.stop_id = "stop-A"
    stu1.arrival.delay = 60
    stu1.arrival.time = 1704067260
    stu1.departure.delay = 90
    stu1.departure.time = 1704067290

    stu2 = entity1.trip_update.stop_time_update.add()
    stu2.stop_sequence = 2
    stu2.stop_id = "stop-B"
    stu2.arrival.delay = 120
    stu2.arrival.time = 1704067500

    # Entity without stop time updates
    entity2 = feed.entity.add()
    entity2.id = "trip-update-2"
    entity2.trip_update.trip.trip_id = "trip-789"
    entity2.trip_update.trip.route_id = "route-C"

    return feed


@pytest.fixture
def sample_service_alert_feed() -> gtfs_realtime_pb2.FeedMessage:
    """Create a sample FeedMessage with service alerts."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1704067200

    # Alert with informed entities
    entity1 = feed.entity.add()
    entity1.id = "alert-1"
    entity1.alert.cause = gtfs_realtime_pb2.Alert.CONSTRUCTION
    entity1.alert.effect = gtfs_realtime_pb2.Alert.DETOUR
    entity1.alert.severity_level = gtfs_realtime_pb2.Alert.WARNING

    # Active period
    ap = entity1.alert.active_period.add()
    ap.start = 1704067200
    ap.end = 1704153600

    # Header and description
    header = entity1.alert.header_text.translation.add()
    header.text = "Construction on Main St"
    header.language = "en"

    desc = entity1.alert.description_text.translation.add()
    desc.text = "Route detoured due to construction work"
    desc.language = "en"

    # Informed entities
    ie1 = entity1.alert.informed_entity.add()
    ie1.agency_id = "agency-1"
    ie1.route_id = "route-A"

    ie2 = entity1.alert.informed_entity.add()
    ie2.stop_id = "stop-100"

    # Alert without informed entities
    entity2 = feed.entity.add()
    entity2.id = "alert-2"
    entity2.alert.cause = gtfs_realtime_pb2.Alert.OTHER_CAUSE
    entity2.alert.effect = gtfs_realtime_pb2.Alert.UNKNOWN_EFFECT
    header2 = entity2.alert.header_text.translation.add()
    header2.text = "General announcement"

    return feed


@pytest.fixture
def empty_feed() -> gtfs_realtime_pb2.FeedMessage:
    """Create an empty FeedMessage."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1704067200
    return feed
