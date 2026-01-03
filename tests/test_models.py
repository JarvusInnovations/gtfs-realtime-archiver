"""Tests for pydantic models."""

from typing import Any

import pytest
from pydantic import ValidationError

from gtfs_rt_archiver.models import (
    AgenciesFileConfig,
    AgencyConfig,
    DefaultsConfig,
    FeedConfig,
    FeedType,
    IntervalDefaults,
    RealtimeFeedConfig,
    RetryConfig,
    SystemConfig,
)


class TestFeedType:
    """Tests for FeedType enum."""

    def test_valid_feed_types(self) -> None:
        """Test that all expected feed types exist."""
        assert FeedType.VEHICLE_POSITIONS == "vehicle_positions"
        assert FeedType.TRIP_UPDATES == "trip_updates"
        assert FeedType.SERVICE_ALERTS == "service_alerts"

    def test_feed_type_from_string(self) -> None:
        """Test creating FeedType from string value."""
        assert FeedType("vehicle_positions") == FeedType.VEHICLE_POSITIONS


class TestRetryConfig:
    """Tests for RetryConfig model."""

    def test_default_values(self) -> None:
        """Test default retry configuration values."""
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.backoff_base == 1.0
        assert config.backoff_max == 10.0

    def test_custom_values(self) -> None:
        """Test custom retry configuration values."""
        config = RetryConfig(max_attempts=5, backoff_base=2.0, backoff_max=30.0)
        assert config.max_attempts == 5
        assert config.backoff_base == 2.0
        assert config.backoff_max == 30.0

    def test_validation_max_attempts_bounds(self) -> None:
        """Test max_attempts validation bounds."""
        with pytest.raises(ValidationError):
            RetryConfig(max_attempts=0)
        with pytest.raises(ValidationError):
            RetryConfig(max_attempts=11)

    def test_validation_backoff_bounds(self) -> None:
        """Test backoff validation bounds."""
        with pytest.raises(ValidationError):
            RetryConfig(backoff_base=0.05)
        with pytest.raises(ValidationError):
            RetryConfig(backoff_max=61.0)


class TestIntervalDefaults:
    """Tests for IntervalDefaults model."""

    def test_default_values(self) -> None:
        """Test default interval values."""
        config = IntervalDefaults()
        assert config.vehicle_positions == 20
        assert config.trip_updates == 20
        assert config.service_alerts == 60

    def test_custom_values(self) -> None:
        """Test custom interval values."""
        config = IntervalDefaults(
            vehicle_positions=15,
            trip_updates=30,
            service_alerts=120,
        )
        assert config.vehicle_positions == 15
        assert config.trip_updates == 30
        assert config.service_alerts == 120

    def test_get_interval(self) -> None:
        """Test get_interval method."""
        config = IntervalDefaults(
            vehicle_positions=10,
            trip_updates=20,
            service_alerts=30,
        )
        assert config.get_interval(FeedType.VEHICLE_POSITIONS) == 10
        assert config.get_interval(FeedType.TRIP_UPDATES) == 20
        assert config.get_interval(FeedType.SERVICE_ALERTS) == 30

    def test_validation_bounds(self) -> None:
        """Test interval validation bounds."""
        with pytest.raises(ValidationError):
            IntervalDefaults(vehicle_positions=4)
        with pytest.raises(ValidationError):
            IntervalDefaults(service_alerts=3601)


class TestFeedConfig:
    """Tests for FeedConfig model (flattened format)."""

    def test_minimal_config(self, sample_feed_config: dict[str, Any]) -> None:
        """Test creating a feed config with minimal required fields."""
        config = FeedConfig(**sample_feed_config)
        assert config.id == "test-agency-vehicle-positions"
        assert config.name == "Test Agency Vehicle Positions"
        assert str(config.url) == "https://example.com/feed.pb"
        assert config.feed_type == FeedType.VEHICLE_POSITIONS
        assert config.agency_id == "test-agency"
        assert config.agency_name == "Test Agency"

    def test_default_values(self, sample_feed_config: dict[str, Any]) -> None:
        """Test that default values are applied."""
        config = FeedConfig(**sample_feed_config)
        assert config.interval_seconds == 20
        assert config.timeout_seconds == 30
        assert config.retry == RetryConfig()
        assert config.auth is None
        assert config.system_id is None
        assert config.system_name is None
        assert config.schedule_url is None

    def test_with_system(self, sample_feed_config: dict[str, Any]) -> None:
        """Test creating a feed config with system context."""
        sample_feed_config["system_id"] = "bus"
        sample_feed_config["system_name"] = "Bus"
        sample_feed_config["schedule_url"] = "https://example.com/schedule.zip"
        config = FeedConfig(**sample_feed_config)
        assert config.system_id == "bus"
        assert config.system_name == "Bus"
        assert str(config.schedule_url) == "https://example.com/schedule.zip"

    def test_custom_values(self, sample_feed_config: dict[str, Any]) -> None:
        """Test creating a feed config with custom values."""
        sample_feed_config["interval_seconds"] = 60
        sample_feed_config["auth"] = {
            "type": "header",
            "secret_name": "my-api-key",
            "key": "Authorization",
            "value": "Bearer ${SECRET}",
        }
        config = FeedConfig(**sample_feed_config)
        assert config.interval_seconds == 60
        assert config.auth is not None
        assert config.auth.secret_name == "my-api-key"

    def test_id_pattern_validation(self, sample_feed_config: dict[str, Any]) -> None:
        """Test that feed ID must match pattern."""
        sample_feed_config["id"] = "Valid-Feed-123"
        with pytest.raises(ValidationError):
            FeedConfig(**sample_feed_config)

        sample_feed_config["id"] = "valid-feed-123"
        config = FeedConfig(**sample_feed_config)
        assert config.id == "valid-feed-123"

    def test_interval_bounds(self, sample_feed_config: dict[str, Any]) -> None:
        """Test interval_seconds validation bounds."""
        sample_feed_config["interval_seconds"] = 4
        with pytest.raises(ValidationError):
            FeedConfig(**sample_feed_config)

        sample_feed_config["interval_seconds"] = 3601
        with pytest.raises(ValidationError):
            FeedConfig(**sample_feed_config)

    def test_url_validation(self, sample_feed_config: dict[str, Any]) -> None:
        """Test URL validation."""
        sample_feed_config["url"] = "not-a-url"
        with pytest.raises(ValidationError):
            FeedConfig(**sample_feed_config)


class TestRealtimeFeedConfig:
    """Tests for RealtimeFeedConfig model (pre-flattening format)."""

    def test_minimal_config(self) -> None:
        """Test creating with minimal fields."""
        config = RealtimeFeedConfig(
            feed_type=FeedType.VEHICLE_POSITIONS,
            url="https://example.com/feed.pb",
        )
        assert config.feed_type == FeedType.VEHICLE_POSITIONS
        assert config.name is None
        assert config.interval_seconds is None
        assert config.auth is None

    def test_with_overrides(self) -> None:
        """Test creating with override values."""
        config = RealtimeFeedConfig(
            feed_type=FeedType.TRIP_UPDATES,
            url="https://example.com/trips.pb",
            name="Custom Name",
            interval_seconds=10,
        )
        assert config.name == "Custom Name"
        assert config.interval_seconds == 10


class TestSystemConfig:
    """Tests for SystemConfig model."""

    def test_minimal_config(self) -> None:
        """Test creating with minimal fields."""
        config = SystemConfig(
            id="bus",
            name="Bus",
            feeds=[
                RealtimeFeedConfig(
                    feed_type=FeedType.VEHICLE_POSITIONS,
                    url="https://example.com/vehicles.pb",
                ),
            ],
        )
        assert config.id == "bus"
        assert config.name == "Bus"
        assert config.schedule_url is None
        assert config.auth is None
        assert len(config.feeds) == 1

    def test_empty_feeds_raises_error(self) -> None:
        """Test that empty feeds list raises validation error."""
        with pytest.raises(ValidationError, match="at least one feed"):
            SystemConfig(id="bus", name="Bus", feeds=[])

    def test_id_pattern_validation(self) -> None:
        """Test that system ID must match pattern."""
        with pytest.raises(ValidationError):
            SystemConfig(
                id="Invalid_ID",
                name="Test",
                feeds=[
                    RealtimeFeedConfig(
                        feed_type=FeedType.VEHICLE_POSITIONS,
                        url="https://example.com/feed.pb",
                    ),
                ],
            )


class TestAgencyConfig:
    """Tests for AgencyConfig model."""

    def test_simple_agency_with_feeds(self, sample_agency_config: dict[str, Any]) -> None:
        """Test creating a simple agency with direct feeds."""
        config = AgencyConfig(**sample_agency_config)
        assert config.id == "test-agency"
        assert config.name == "Test Agency"
        assert config.feeds is not None
        assert len(config.feeds) == 2
        assert config.systems is None

    def test_agency_with_systems(self, sample_agency_with_systems_config: dict[str, Any]) -> None:
        """Test creating an agency with systems."""
        config = AgencyConfig(**sample_agency_with_systems_config)
        assert config.id == "septa"
        assert config.systems is not None
        assert len(config.systems) == 2
        assert config.feeds is None

    def test_cannot_have_both_feeds_and_systems(self) -> None:
        """Test that agency cannot have both feeds and systems."""
        with pytest.raises(ValidationError, match="cannot have both"):
            AgencyConfig(
                id="test",
                name="Test",
                feeds=[
                    RealtimeFeedConfig(
                        feed_type=FeedType.VEHICLE_POSITIONS,
                        url="https://example.com/vehicles.pb",
                    ),
                ],
                systems=[
                    SystemConfig(
                        id="bus",
                        name="Bus",
                        feeds=[
                            RealtimeFeedConfig(
                                feed_type=FeedType.VEHICLE_POSITIONS,
                                url="https://example.com/bus/vehicles.pb",
                            ),
                        ],
                    ),
                ],
            )

    def test_must_have_feeds_or_systems(self) -> None:
        """Test that agency must have either feeds or systems."""
        with pytest.raises(ValidationError, match="must have either"):
            AgencyConfig(id="test", name="Test")

    def test_id_pattern_validation(self) -> None:
        """Test that agency ID must match pattern."""
        with pytest.raises(ValidationError):
            AgencyConfig(
                id="Invalid_ID",
                name="Test",
                feeds=[
                    RealtimeFeedConfig(
                        feed_type=FeedType.VEHICLE_POSITIONS,
                        url="https://example.com/feed.pb",
                    ),
                ],
            )


class TestDefaultsConfig:
    """Tests for DefaultsConfig model."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = DefaultsConfig()
        assert config.intervals == IntervalDefaults()
        assert config.timeout_seconds == 30
        assert config.retry == RetryConfig()

    def test_custom_values(self) -> None:
        """Test custom default configuration values."""
        config = DefaultsConfig(
            intervals=IntervalDefaults(vehicle_positions=15),
            timeout_seconds=60,
            retry=RetryConfig(max_attempts=5),
        )
        assert config.intervals.vehicle_positions == 15
        assert config.timeout_seconds == 60
        assert config.retry.max_attempts == 5


class TestAgenciesFileConfig:
    """Tests for AgenciesFileConfig model."""

    def test_minimal_config(self, sample_agency_config: dict[str, Any]) -> None:
        """Test creating an agencies file config with minimal fields."""
        config = AgenciesFileConfig(agencies=[sample_agency_config])
        assert len(config.agencies) == 1
        assert config.defaults == DefaultsConfig()

    def test_with_defaults(self, sample_agency_config: dict[str, Any]) -> None:
        """Test creating an agencies file config with custom defaults."""
        config = AgenciesFileConfig(
            defaults={"intervals": {"vehicle_positions": 15}, "timeout_seconds": 45},
            agencies=[sample_agency_config],
        )
        assert config.defaults.intervals.vehicle_positions == 15
        assert config.defaults.timeout_seconds == 45
        assert config.defaults.intervals.trip_updates == 20  # default value

    def test_multiple_agencies(self, sample_agency_config: dict[str, Any]) -> None:
        """Test creating an agencies file config with multiple agencies."""
        agency2 = sample_agency_config.copy()
        agency2["id"] = "another-agency"
        agency2["name"] = "Another Agency"
        config = AgenciesFileConfig(agencies=[sample_agency_config, agency2])
        assert len(config.agencies) == 2
