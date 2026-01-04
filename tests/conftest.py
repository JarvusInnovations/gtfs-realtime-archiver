"""Shared pytest fixtures for GTFS-RT Archiver tests."""

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def sample_feed_config() -> dict[str, Any]:
    """Return a sample feed configuration dictionary (flattened format)."""
    return {
        "id": "test-agency-vehicle-positions",
        "name": "Test Agency Vehicle Positions",
        "url": "https://example.com/feed.pb",
        "feed_type": "vehicle_positions",
        "agency_id": "test-agency",
        "agency_name": "Test Agency",
    }


@pytest.fixture
def sample_agencies_yaml() -> str:
    """Return sample agencies.yaml content."""
    return """
defaults:
  timeout_seconds: 45
  retry:
    max_attempts: 5
    backoff_base: 2.0
    backoff_max: 20.0
  intervals:
    vehicle_positions: 30
    trip_updates: 30
    service_alerts: 90

agencies:
  - id: septa
    name: SEPTA
    schedule_url: https://example.com/septa/schedule.zip
    systems:
      - id: bus
        name: Bus
        schedule_url: https://example.com/septa/bus-schedule.zip
        feeds:
          - feed_type: vehicle_positions
            url: https://example.com/septa/bus/vehicles.pb

  - id: bart
    name: BART
    schedule_url: https://example.com/bart/schedule.zip
    auth:
      type: header
      secret_name: bart-api-key
      key: Authorization
      value: "Bearer ${SECRET}"
    feeds:
      - feed_type: trip_updates
        url: https://example.com/bart/trips.pb
        interval_seconds: 15
"""


@pytest.fixture
def sample_agencies_file(tmp_path: Path, sample_agencies_yaml: str) -> Path:
    """Create a temporary agencies.yaml file."""
    agencies_file = tmp_path / "agencies.yaml"
    agencies_file.write_text(sample_agencies_yaml)
    return agencies_file


@pytest.fixture
def sample_realtime_feed_config() -> dict[str, Any]:
    """Return a sample realtime feed configuration dictionary (pre-flattening)."""
    return {
        "feed_type": "vehicle_positions",
        "url": "https://example.com/feed.pb",
    }


@pytest.fixture
def sample_agency_config() -> dict[str, Any]:
    """Return a sample agency configuration dictionary."""
    return {
        "id": "test-agency",
        "name": "Test Agency",
        "schedule_url": "https://example.com/schedule.zip",
        "feeds": [
            {
                "feed_type": "vehicle_positions",
                "url": "https://example.com/vehicles.pb",
            },
            {
                "feed_type": "trip_updates",
                "url": "https://example.com/trips.pb",
            },
        ],
    }


@pytest.fixture
def sample_agency_with_systems_config() -> dict[str, Any]:
    """Return a sample agency configuration with systems."""
    return {
        "id": "septa",
        "name": "SEPTA",
        "systems": [
            {
                "id": "bus",
                "name": "Bus",
                "schedule_url": "https://example.com/bus-schedule.zip",
                "feeds": [
                    {
                        "feed_type": "vehicle_positions",
                        "url": "https://example.com/bus/vehicles.pb",
                    },
                ],
            },
            {
                "id": "rail",
                "name": "Regional Rail",
                "schedule_url": "https://example.com/rail-schedule.zip",
                "feeds": [
                    {
                        "feed_type": "vehicle_positions",
                        "url": "https://example.com/rail/vehicles.pb",
                    },
                ],
            },
        ],
    }
