"""Tests for pydantic models."""

from typing import Any

import pytest
from pydantic import ValidationError

from gtfs_rt_archiver.models import (
    DefaultsConfig,
    FeedConfig,
    FeedsFileConfig,
    FeedType,
    RetryConfig,
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


class TestFeedConfig:
    """Tests for FeedConfig model."""

    def test_minimal_config(self, sample_feed_config: dict[str, Any]) -> None:
        """Test creating a feed config with minimal required fields."""
        config = FeedConfig(**sample_feed_config)
        assert config.id == "test-feed"
        assert config.name == "Test Feed"
        assert str(config.url) == "https://example.com/feed.pb"
        assert config.feed_type == FeedType.VEHICLE_POSITIONS
        assert config.agency == "test-agency"

    def test_default_values(self, sample_feed_config: dict[str, Any]) -> None:
        """Test that default values are applied."""
        config = FeedConfig(**sample_feed_config)
        assert config.interval_seconds == 20
        assert config.timeout_seconds == 30
        assert config.retry == RetryConfig()
        assert config.auth is None

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


class TestDefaultsConfig:
    """Tests for DefaultsConfig model."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = DefaultsConfig()
        assert config.interval_seconds == 20
        assert config.timeout_seconds == 30
        assert config.retry == RetryConfig()

    def test_custom_values(self) -> None:
        """Test custom default configuration values."""
        config = DefaultsConfig(
            interval_seconds=45,
            timeout_seconds=60,
            retry=RetryConfig(max_attempts=5),
        )
        assert config.interval_seconds == 45
        assert config.timeout_seconds == 60
        assert config.retry.max_attempts == 5


class TestFeedsFileConfig:
    """Tests for FeedsFileConfig model."""

    def test_minimal_config(self, sample_feed_config: dict[str, Any]) -> None:
        """Test creating a feeds file config with minimal fields."""
        config = FeedsFileConfig(feeds=[sample_feed_config])
        assert len(config.feeds) == 1
        assert config.defaults == DefaultsConfig()

    def test_with_defaults(self, sample_feed_config: dict[str, Any]) -> None:
        """Test creating a feeds file config with custom defaults."""
        config = FeedsFileConfig(
            defaults={"interval_seconds": 45},
            feeds=[sample_feed_config],
        )
        assert config.defaults.interval_seconds == 45
        assert config.defaults.timeout_seconds == 30  # default value

    def test_multiple_feeds(self, sample_feed_config: dict[str, Any]) -> None:
        """Test creating a feeds file config with multiple feeds."""
        feed2 = sample_feed_config.copy()
        feed2["id"] = "another-feed"
        config = FeedsFileConfig(feeds=[sample_feed_config, feed2])
        assert len(config.feeds) == 2
