"""Shared pytest fixtures for GTFS-RT Archiver tests."""

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def sample_feed_config() -> dict[str, Any]:
    """Return a sample feed configuration dictionary."""
    return {
        "id": "test-feed",
        "name": "Test Feed",
        "url": "https://example.com/feed.pb",
        "feed_type": "vehicle_positions",
        "agency": "test-agency",
    }


@pytest.fixture
def sample_feeds_yaml() -> str:
    """Return sample feeds.yaml content."""
    return """
defaults:
  interval_seconds: 30
  timeout_seconds: 45
  retry:
    max_attempts: 5
    backoff_base: 2.0
    backoff_max: 20.0

feeds:
  - id: septa-vehicles
    name: SEPTA Vehicle Positions
    url: https://example.com/septa/vehicles.pb
    feed_type: vehicle_positions
    agency: septa

  - id: bart-trips
    name: BART Trip Updates
    url: https://example.com/bart/trips.pb
    feed_type: trip_updates
    agency: bart
    interval_seconds: 15
    headers:
      Authorization: "Bearer ${TEST_API_KEY}"
"""


@pytest.fixture
def sample_feeds_file(tmp_path: Path, sample_feeds_yaml: str) -> Path:
    """Create a temporary feeds.yaml file."""
    feeds_file = tmp_path / "feeds.yaml"
    feeds_file.write_text(sample_feeds_yaml)
    return feeds_file
