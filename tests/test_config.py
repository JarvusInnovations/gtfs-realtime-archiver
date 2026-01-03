"""Tests for configuration loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from gtfs_rt_archiver.config import (
    Settings,
    flatten_agencies,
    generate_feed_id,
    generate_feed_name,
    load_agencies_file,
)
from gtfs_rt_archiver.models import (
    AgenciesFileConfig,
    AgencyConfig,
    AuthType,
    DefaultsConfig,
    FeedType,
    IntervalDefaults,
    RealtimeFeedConfig,
    SystemConfig,
)


class TestLoadAgenciesFile:
    """Tests for loading agencies.yaml files."""

    def test_load_valid_file(self, sample_agencies_file: Path) -> None:
        """Test loading a valid agencies.yaml file."""
        config = load_agencies_file(sample_agencies_file)

        assert config.defaults.timeout_seconds == 45
        assert config.defaults.retry.max_attempts == 5
        assert config.defaults.intervals.vehicle_positions == 30
        assert config.defaults.intervals.service_alerts == 90

        assert len(config.agencies) == 2

        septa = config.agencies[0]
        assert septa.id == "septa"
        assert septa.systems is not None
        assert len(septa.systems) == 1
        assert septa.systems[0].id == "bus"

        bart = config.agencies[1]
        assert bart.id == "bart"
        assert bart.feeds is not None
        assert len(bart.feeds) == 1
        assert bart.auth is not None
        assert bart.auth.type == AuthType.HEADER
        assert bart.auth.secret_name == "bart-api-key"

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Test that FileNotFoundError is raised for missing file."""
        with pytest.raises(FileNotFoundError):
            load_agencies_file(tmp_path / "nonexistent.yaml")


class TestGenerateFeedId:
    """Tests for feed ID generation."""

    def test_simple_agency(self) -> None:
        """Test ID generation for simple agency without system."""
        feed_id = generate_feed_id("bart", None, FeedType.VEHICLE_POSITIONS)
        assert feed_id == "bart-vehicle-positions"

    def test_agency_with_system(self) -> None:
        """Test ID generation for agency with system."""
        feed_id = generate_feed_id("septa", "bus", FeedType.TRIP_UPDATES)
        assert feed_id == "septa-bus-trip-updates"

    def test_service_alerts(self) -> None:
        """Test ID generation for service alerts."""
        feed_id = generate_feed_id("mta", "subway", FeedType.SERVICE_ALERTS)
        assert feed_id == "mta-subway-service-alerts"


class TestGenerateFeedName:
    """Tests for feed name generation."""

    def test_simple_agency(self) -> None:
        """Test name generation for simple agency without system."""
        name = generate_feed_name("BART", None, FeedType.VEHICLE_POSITIONS)
        assert name == "BART Vehicle Positions"

    def test_agency_with_system(self) -> None:
        """Test name generation for agency with system."""
        name = generate_feed_name("SEPTA", "Bus", FeedType.TRIP_UPDATES)
        assert name == "SEPTA Bus Trip Updates"


class TestFlattenAgencies:
    """Tests for flattening agency hierarchy."""

    def test_flatten_simple_agency(self) -> None:
        """Test flattening a simple agency with direct feeds."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(),
            agencies=[
                AgencyConfig(
                    id="bart",
                    name="BART",
                    schedule_url="https://example.com/schedule.zip",
                    feeds=[
                        RealtimeFeedConfig(
                            feed_type=FeedType.VEHICLE_POSITIONS,
                            url="https://example.com/vehicles.pb",
                        ),
                        RealtimeFeedConfig(
                            feed_type=FeedType.TRIP_UPDATES,
                            url="https://example.com/trips.pb",
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)

        assert len(feeds) == 2

        vehicles = feeds[0]
        assert vehicles.id == "bart-vehicle-positions"
        assert vehicles.name == "BART Vehicle Positions"
        assert vehicles.agency_id == "bart"
        assert vehicles.agency_name == "BART"
        assert vehicles.system_id is None
        assert vehicles.system_name is None
        assert str(vehicles.schedule_url) == "https://example.com/schedule.zip"
        assert vehicles.interval_seconds == 20  # Default for vehicle_positions

        trips = feeds[1]
        assert trips.id == "bart-trip-updates"
        assert trips.interval_seconds == 20  # Default for trip_updates

    def test_flatten_agency_with_systems(self) -> None:
        """Test flattening an agency with systems."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(),
            agencies=[
                AgencyConfig(
                    id="septa",
                    name="SEPTA",
                    systems=[
                        SystemConfig(
                            id="bus",
                            name="Bus",
                            schedule_url="https://example.com/bus-schedule.zip",
                            feeds=[
                                RealtimeFeedConfig(
                                    feed_type=FeedType.VEHICLE_POSITIONS,
                                    url="https://example.com/bus/vehicles.pb",
                                ),
                            ],
                        ),
                        SystemConfig(
                            id="rail",
                            name="Regional Rail",
                            schedule_url="https://example.com/rail-schedule.zip",
                            feeds=[
                                RealtimeFeedConfig(
                                    feed_type=FeedType.VEHICLE_POSITIONS,
                                    url="https://example.com/rail/vehicles.pb",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)

        assert len(feeds) == 2

        bus = feeds[0]
        assert bus.id == "septa-bus-vehicle-positions"
        assert bus.name == "SEPTA Bus Vehicle Positions"
        assert bus.agency_id == "septa"
        assert bus.system_id == "bus"
        assert bus.system_name == "Bus"
        assert str(bus.schedule_url) == "https://example.com/bus-schedule.zip"

        rail = feeds[1]
        assert rail.id == "septa-rail-vehicle-positions"
        assert rail.system_id == "rail"
        assert rail.system_name == "Regional Rail"

    def test_interval_defaults_by_feed_type(self) -> None:
        """Test that different feed types get different default intervals."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(
                intervals=IntervalDefaults(
                    vehicle_positions=20,
                    trip_updates=30,
                    service_alerts=60,
                ),
            ),
            agencies=[
                AgencyConfig(
                    id="test",
                    name="Test Agency",
                    feeds=[
                        RealtimeFeedConfig(
                            feed_type=FeedType.VEHICLE_POSITIONS,
                            url="https://example.com/vehicles.pb",
                        ),
                        RealtimeFeedConfig(
                            feed_type=FeedType.TRIP_UPDATES,
                            url="https://example.com/trips.pb",
                        ),
                        RealtimeFeedConfig(
                            feed_type=FeedType.SERVICE_ALERTS,
                            url="https://example.com/alerts.pb",
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)

        assert feeds[0].interval_seconds == 20  # vehicle_positions
        assert feeds[1].interval_seconds == 30  # trip_updates
        assert feeds[2].interval_seconds == 60  # service_alerts

    def test_feed_interval_override(self) -> None:
        """Test that feed-level interval overrides feed-type default."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(
                intervals=IntervalDefaults(vehicle_positions=20),
            ),
            agencies=[
                AgencyConfig(
                    id="test",
                    name="Test Agency",
                    feeds=[
                        RealtimeFeedConfig(
                            feed_type=FeedType.VEHICLE_POSITIONS,
                            url="https://example.com/vehicles.pb",
                            interval_seconds=10,  # Override
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)
        assert feeds[0].interval_seconds == 10

    def test_auth_inheritance_from_agency(self) -> None:
        """Test that auth is inherited from agency to feeds."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(),
            agencies=[
                AgencyConfig(
                    id="test",
                    name="Test Agency",
                    auth={
                        "type": "header",
                        "secret_name": "agency-key",
                        "key": "x-api-key",
                    },
                    feeds=[
                        RealtimeFeedConfig(
                            feed_type=FeedType.VEHICLE_POSITIONS,
                            url="https://example.com/vehicles.pb",
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)
        assert feeds[0].auth is not None
        assert feeds[0].auth.secret_name == "agency-key"

    def test_auth_inheritance_from_system(self) -> None:
        """Test that system auth overrides agency auth."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(),
            agencies=[
                AgencyConfig(
                    id="test",
                    name="Test Agency",
                    auth={
                        "type": "header",
                        "secret_name": "agency-key",
                        "key": "x-api-key",
                    },
                    systems=[
                        SystemConfig(
                            id="system1",
                            name="System 1",
                            auth={
                                "type": "query",
                                "secret_name": "system-key",
                                "key": "api_key",
                            },
                            feeds=[
                                RealtimeFeedConfig(
                                    feed_type=FeedType.VEHICLE_POSITIONS,
                                    url="https://example.com/vehicles.pb",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)
        assert feeds[0].auth is not None
        assert feeds[0].auth.secret_name == "system-key"
        assert feeds[0].auth.type == AuthType.QUERY

    def test_auth_override_at_feed_level(self) -> None:
        """Test that feed auth overrides system/agency auth."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(),
            agencies=[
                AgencyConfig(
                    id="test",
                    name="Test Agency",
                    auth={
                        "type": "header",
                        "secret_name": "agency-key",
                        "key": "x-api-key",
                    },
                    feeds=[
                        RealtimeFeedConfig(
                            feed_type=FeedType.VEHICLE_POSITIONS,
                            url="https://example.com/vehicles.pb",
                            auth={
                                "type": "query",
                                "secret_name": "feed-key",
                                "key": "token",
                            },
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)
        assert feeds[0].auth is not None
        assert feeds[0].auth.secret_name == "feed-key"

    def test_custom_feed_name(self) -> None:
        """Test that custom feed name is used when provided."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(),
            agencies=[
                AgencyConfig(
                    id="test",
                    name="Test Agency",
                    feeds=[
                        RealtimeFeedConfig(
                            feed_type=FeedType.VEHICLE_POSITIONS,
                            url="https://example.com/vehicles.pb",
                            name="Custom Feed Name",
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)
        assert feeds[0].name == "Custom Feed Name"

    def test_schedule_url_from_system_overrides_agency(self) -> None:
        """Test that system schedule_url overrides agency schedule_url."""
        config = AgenciesFileConfig(
            defaults=DefaultsConfig(),
            agencies=[
                AgencyConfig(
                    id="septa",
                    name="SEPTA",
                    schedule_url="https://example.com/agency-schedule.zip",
                    systems=[
                        SystemConfig(
                            id="bus",
                            name="Bus",
                            schedule_url="https://example.com/bus-schedule.zip",
                            feeds=[
                                RealtimeFeedConfig(
                                    feed_type=FeedType.VEHICLE_POSITIONS,
                                    url="https://example.com/vehicles.pb",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

        feeds = flatten_agencies(config)
        assert str(feeds[0].schedule_url) == "https://example.com/bus-schedule.zip"


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test default settings values."""
        monkeypatch.setenv("GCS_BUCKET", "test-bucket")

        settings = Settings()

        assert settings.config_path == Path("./agencies.yaml")
        assert settings.gcs_bucket == "test-bucket"
        assert settings.max_concurrent == 100
        assert settings.health_port == 8080
        assert settings.log_level == "INFO"
        assert settings.log_format == "json"
        assert settings.shard_index == 0
        assert settings.total_shards == 1

    def test_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test custom settings from environment."""
        monkeypatch.setenv("CONFIG_PATH", "/etc/agencies.yaml")
        monkeypatch.setenv("GCS_BUCKET", "my-bucket")
        monkeypatch.setenv("MAX_CONCURRENT", "50")
        monkeypatch.setenv("HEALTH_PORT", "9000")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_FORMAT", "text")

        settings = Settings()

        assert settings.config_path == Path("/etc/agencies.yaml")
        assert settings.gcs_bucket == "my-bucket"
        assert settings.max_concurrent == 50
        assert settings.health_port == 9000
        assert settings.log_level == "DEBUG"
        assert settings.log_format == "text"

    def test_missing_required_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing GCS_BUCKET raises error."""
        # Clear the environment variable
        monkeypatch.delenv("GCS_BUCKET", raising=False)

        with pytest.raises(ValidationError):
            Settings()

    def test_shard_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test shard configuration validation."""
        monkeypatch.setenv("GCS_BUCKET", "test-bucket")
        monkeypatch.setenv("SHARD_INDEX", "2")
        monkeypatch.setenv("TOTAL_SHARDS", "2")

        with pytest.raises(ValidationError, match="shard_index.*must be less than.*total_shards"):
            Settings()

    def test_valid_shard_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test valid shard configuration."""
        monkeypatch.setenv("GCS_BUCKET", "test-bucket")
        monkeypatch.setenv("SHARD_INDEX", "1")
        monkeypatch.setenv("TOTAL_SHARDS", "3")

        settings = Settings()
        assert settings.shard_index == 1
        assert settings.total_shards == 3
