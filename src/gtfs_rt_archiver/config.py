"""Configuration loading and settings for GTFS-RT Archiver."""

import os
import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gtfs_rt_archiver.models import (
    DefaultsConfig,
    FeedConfig,
    FeedsFileConfig,
    RetryConfig,
)


def substitute_env_vars(value: str) -> str:
    """Substitute ${VAR} patterns with environment variable values.

    Args:
        value: String potentially containing ${VAR} patterns.

    Returns:
        String with environment variables substituted.

    Raises:
        ValueError: If an environment variable is not set.
    """
    pattern = re.compile(r"\$\{([^}]+)\}")

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable '{var_name}' is not set")
        return env_value

    return pattern.sub(replacer, value)


def substitute_env_vars_in_dict(d: dict[str, str]) -> dict[str, str]:
    """Substitute environment variables in all values of a dictionary."""
    return {k: substitute_env_vars(v) for k, v in d.items()}


def load_feeds_file(path: Path) -> FeedsFileConfig:
    """Load and parse a feeds.yaml configuration file.

    Args:
        path: Path to the feeds.yaml file.

    Returns:
        Parsed FeedsFileConfig with environment variables substituted.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
        ValueError: If environment variables are not set.
        pydantic.ValidationError: If the configuration is invalid.
    """
    with path.open() as f:
        raw_config = yaml.safe_load(f)

    # Substitute environment variables in headers and query_params
    if "feeds" in raw_config:
        for feed in raw_config["feeds"]:
            if "headers" in feed:
                feed["headers"] = substitute_env_vars_in_dict(feed["headers"])
            if "query_params" in feed:
                feed["query_params"] = substitute_env_vars_in_dict(feed["query_params"])

    return FeedsFileConfig.model_validate(raw_config)


def apply_defaults(feed: FeedConfig, defaults: DefaultsConfig) -> FeedConfig:
    """Apply default values to a feed configuration.

    Only applies defaults for fields that were not explicitly set in the feed config.
    This is a simplified approach - we create a new FeedConfig with defaults applied.
    """
    # Create feed dict and apply defaults for unset fields
    feed_dict = feed.model_dump()

    # Check if interval_seconds was explicitly set (not the model default)
    # We use the model's default value to detect if it was explicitly set
    if feed.interval_seconds == FeedConfig.model_fields["interval_seconds"].default:
        feed_dict["interval_seconds"] = defaults.interval_seconds

    if feed.timeout_seconds == FeedConfig.model_fields["timeout_seconds"].default:
        feed_dict["timeout_seconds"] = defaults.timeout_seconds

    # For retry, merge the defaults
    if feed.retry == RetryConfig():
        feed_dict["retry"] = defaults.retry.model_dump()

    return FeedConfig.model_validate(feed_dict)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=True,
    )

    # Feed configuration
    config_path: Path = Field(
        default=Path("./feeds.yaml"),
        validation_alias="CONFIG_PATH",
        description="Path to feeds.yaml configuration file",
    )

    # GCS settings
    gcs_bucket: str = Field(
        validation_alias="GCS_BUCKET",
        description="Target GCS bucket for archived feeds",
    )

    # Runtime settings
    max_concurrent: int = Field(
        default=100,
        ge=1,
        le=500,
        validation_alias="MAX_CONCURRENT",
        description="Maximum number of concurrent feed fetches",
    )

    # Server settings
    health_port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        validation_alias="HEALTH_PORT",
        description="Port for health check and metrics server",
    )

    # Logging settings
    log_level: str = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    log_format: str = Field(
        default="json",
        validation_alias="LOG_FORMAT",
        description="Log output format (json or text)",
    )

    # Sharding settings (for multi-instance deployments)
    shard_index: int = Field(
        default=0,
        ge=0,
        validation_alias="SHARD_INDEX",
        description="Index of this shard (0-based)",
    )
    total_shards: int = Field(
        default=1,
        ge=1,
        validation_alias="TOTAL_SHARDS",
        description="Total number of shards",
    )

    @model_validator(mode="after")
    def validate_shard_config(self) -> Self:
        """Validate that shard_index is less than total_shards."""
        if self.shard_index >= self.total_shards:
            raise ValueError(
                f"shard_index ({self.shard_index}) must be less than "
                f"total_shards ({self.total_shards})"
            )
        return self
