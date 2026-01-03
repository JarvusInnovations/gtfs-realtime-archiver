"""Tests for configuration loading."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from gtfs_rt_archiver.config import (
    Settings,
    apply_defaults,
    load_feeds_file,
    substitute_env_vars,
    substitute_env_vars_in_dict,
)
from gtfs_rt_archiver.models import DefaultsConfig, FeedConfig, RetryConfig


class TestSubstituteEnvVars:
    """Tests for environment variable substitution."""

    def test_single_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting a single environment variable."""
        monkeypatch.setenv("TEST_VAR", "test_value")
        result = substitute_env_vars("Bearer ${TEST_VAR}")
        assert result == "Bearer test_value"

    def test_multiple_substitutions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting multiple environment variables."""
        monkeypatch.setenv("USER", "admin")
        monkeypatch.setenv("PASS", "secret")
        result = substitute_env_vars("${USER}:${PASS}")
        assert result == "admin:secret"

    def test_no_substitution_needed(self) -> None:
        """Test string without environment variables."""
        result = substitute_env_vars("plain string")
        assert result == "plain string"

    def test_missing_env_var_raises(self) -> None:
        """Test that missing environment variable raises ValueError."""
        # Ensure the variable doesn't exist
        if "NONEXISTENT_VAR" in os.environ:
            del os.environ["NONEXISTENT_VAR"]

        with pytest.raises(ValueError, match="Environment variable 'NONEXISTENT_VAR' is not set"):
            substitute_env_vars("${NONEXISTENT_VAR}")


class TestSubstituteEnvVarsInDict:
    """Tests for dictionary environment variable substitution."""

    def test_dict_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting environment variables in dictionary values."""
        monkeypatch.setenv("API_KEY", "abc123")
        result = substitute_env_vars_in_dict(
            {"Authorization": "Bearer ${API_KEY}", "Content-Type": "application/json"}
        )
        assert result == {
            "Authorization": "Bearer abc123",
            "Content-Type": "application/json",
        }


class TestLoadFeedsFile:
    """Tests for loading feeds.yaml files."""

    def test_load_valid_file(
        self, sample_feeds_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test loading a valid feeds.yaml file."""
        monkeypatch.setenv("TEST_API_KEY", "secret123")

        config = load_feeds_file(sample_feeds_file)

        assert config.defaults.interval_seconds == 30
        assert config.defaults.timeout_seconds == 45
        assert config.defaults.retry.max_attempts == 5

        assert len(config.feeds) == 2

        septa = config.feeds[0]
        assert septa.id == "septa-vehicles"
        assert septa.feed_type.value == "vehicle_positions"
        assert septa.agency == "septa"

        bart = config.feeds[1]
        assert bart.id == "bart-trips"
        assert bart.interval_seconds == 15
        assert bart.headers == {"Authorization": "Bearer secret123"}

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Test that FileNotFoundError is raised for missing file."""
        with pytest.raises(FileNotFoundError):
            load_feeds_file(tmp_path / "nonexistent.yaml")

    def test_missing_env_var_in_file(self, sample_feeds_file: Path) -> None:
        """Test that missing env var in file raises ValueError."""
        # Don't set TEST_API_KEY
        if "TEST_API_KEY" in os.environ:
            del os.environ["TEST_API_KEY"]

        with pytest.raises(ValueError, match="Environment variable 'TEST_API_KEY' is not set"):
            load_feeds_file(sample_feeds_file)


class TestApplyDefaults:
    """Tests for applying default values to feed configs."""

    def test_applies_interval_default(self) -> None:
        """Test that interval_seconds default is applied."""
        feed = FeedConfig(
            id="test",
            name="Test",
            url="https://example.com/feed.pb",
            feed_type="vehicle_positions",
        )
        defaults = DefaultsConfig(interval_seconds=45)

        result = apply_defaults(feed, defaults)
        assert result.interval_seconds == 45

    def test_explicit_value_not_overridden(self) -> None:
        """Test that explicitly set values are not overridden."""
        feed = FeedConfig(
            id="test",
            name="Test",
            url="https://example.com/feed.pb",
            feed_type="vehicle_positions",
            interval_seconds=60,
        )
        defaults = DefaultsConfig(interval_seconds=45)

        result = apply_defaults(feed, defaults)
        assert result.interval_seconds == 60

    def test_applies_retry_defaults(self) -> None:
        """Test that retry defaults are applied."""
        feed = FeedConfig(
            id="test",
            name="Test",
            url="https://example.com/feed.pb",
            feed_type="vehicle_positions",
        )
        defaults = DefaultsConfig(retry=RetryConfig(max_attempts=5, backoff_base=2.0))

        result = apply_defaults(feed, defaults)
        assert result.retry.max_attempts == 5
        assert result.retry.backoff_base == 2.0


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test default settings values."""
        monkeypatch.setenv("GCS_BUCKET", "test-bucket")

        settings = Settings()

        assert settings.config_path == Path("./feeds.yaml")
        assert settings.gcs_bucket == "test-bucket"
        assert settings.max_concurrent == 100
        assert settings.health_port == 8080
        assert settings.log_level == "INFO"
        assert settings.log_format == "json"
        assert settings.shard_index == 0
        assert settings.total_shards == 1

    def test_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test custom settings from environment."""
        monkeypatch.setenv("CONFIG_PATH", "/etc/feeds.yaml")
        monkeypatch.setenv("GCS_BUCKET", "my-bucket")
        monkeypatch.setenv("MAX_CONCURRENT", "50")
        monkeypatch.setenv("HEALTH_PORT", "9000")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_FORMAT", "text")

        settings = Settings()

        assert settings.config_path == Path("/etc/feeds.yaml")
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
