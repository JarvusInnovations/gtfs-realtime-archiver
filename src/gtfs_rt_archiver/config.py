"""Configuration loading and settings for GTFS-RT Archiver."""

import asyncio
from pathlib import Path
from typing import Self

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gtfs_rt_archiver.models import (
    AgenciesFileConfig,
    AgencyConfig,
    AuthConfig,
    DefaultsConfig,
    FeedConfig,
    FeedType,
    RealtimeFeedConfig,
    SystemConfig,
)


def load_agencies_file(path: Path) -> AgenciesFileConfig:
    """Load and parse an agencies.yaml configuration file.

    Args:
        path: Path to the agencies.yaml file.

    Returns:
        Parsed AgenciesFileConfig.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
        pydantic.ValidationError: If the configuration is invalid.

    Note:
        This only loads and validates the YAML. Secrets must be resolved
        separately by calling resolve_feed_secrets().
    """
    with path.open() as f:
        raw_config = yaml.safe_load(f)

    return AgenciesFileConfig.model_validate(raw_config)


def generate_feed_id(
    agency_id: str,
    system_id: str | None,
    feed_type: FeedType,
) -> str:
    """Generate a feed ID from agency, system, and feed type.

    Args:
        agency_id: The agency identifier.
        system_id: The system identifier (optional).
        feed_type: The feed type.

    Returns:
        Feed ID in format: {agency_id}[-{system_id}]-{feed_type}
    """
    parts = [agency_id]
    if system_id:
        parts.append(system_id)
    # Convert feed_type value (e.g., "vehicle_positions") to hyphenated form
    parts.append(feed_type.value.replace("_", "-"))
    return "-".join(parts)


def generate_feed_name(
    agency_name: str,
    system_name: str | None,
    feed_type: FeedType,
) -> str:
    """Generate a human-readable feed name.

    Args:
        agency_name: The agency name.
        system_name: The system name (optional).
        feed_type: The feed type.

    Returns:
        Feed name in format: {Agency Name} [{System Name}] {Feed Type Title}
    """
    # Convert feed_type value (e.g., "vehicle_positions") to title case
    type_name = feed_type.value.replace("_", " ").title()
    if system_name:
        return f"{agency_name} {system_name} {type_name}"
    return f"{agency_name} {type_name}"


def _resolve_auth(
    feed_auth: AuthConfig | None,
    system_auth: AuthConfig | None,
    agency_auth: AuthConfig | None,
) -> AuthConfig | None:
    """Resolve auth configuration with inheritance: feed > system > agency."""
    if feed_auth is not None:
        return feed_auth
    if system_auth is not None:
        return system_auth
    return agency_auth


def _flatten_feed(
    feed: RealtimeFeedConfig,
    agency: AgencyConfig,
    system: SystemConfig | None,
    defaults: DefaultsConfig,
) -> FeedConfig:
    """Flatten a single feed with all inheritance applied.

    Args:
        feed: The realtime feed configuration.
        agency: The parent agency configuration.
        system: The parent system configuration (optional).
        defaults: Global default configuration.

    Returns:
        A flattened FeedConfig ready for runtime use.
    """
    # Generate ID and name
    feed_id = generate_feed_id(
        agency.id,
        system.id if system else None,
        feed.feed_type,
    )
    feed_name = feed.name or generate_feed_name(
        agency.name,
        system.name if system else None,
        feed.feed_type,
    )

    # Resolve interval (feed > feed-type default)
    interval = feed.interval_seconds
    if interval is None:
        interval = defaults.intervals.get_interval(feed.feed_type)

    # Resolve timeout (feed > global default)
    timeout = feed.timeout_seconds
    if timeout is None:
        timeout = defaults.timeout_seconds

    # Resolve retry (feed > global default)
    retry = feed.retry or defaults.retry

    # Resolve auth (feed > system > agency)
    auth = _resolve_auth(
        feed.auth,
        system.auth if system else None,
        agency.auth,
    )

    # Resolve schedule_url (system > agency)
    schedule_url = None
    if system and system.schedule_url:
        schedule_url = system.schedule_url
    elif agency.schedule_url:
        schedule_url = agency.schedule_url

    return FeedConfig(
        id=feed_id,
        name=feed_name,
        url=feed.url,
        feed_type=feed.feed_type,
        agency_id=agency.id,
        agency_name=agency.name,
        system_id=system.id if system else None,
        system_name=system.name if system else None,
        schedule_url=schedule_url,
        interval_seconds=interval,
        timeout_seconds=timeout,
        retry=retry,
        auth=auth,
    )


def flatten_agencies(config: AgenciesFileConfig) -> list[FeedConfig]:
    """Flatten agency hierarchy into a list of FeedConfig objects.

    Applies defaults and resolves inheritance:
    1. Global defaults (intervals.{feed_type}, timeout_seconds, retry)
    2. Agency-level auth (inherited by systems/feeds)
    3. System-level auth (inherited by feeds, overrides agency)
    4. Feed-level settings (override everything)

    Args:
        config: The parsed agencies configuration.

    Returns:
        List of flattened FeedConfig objects ready for runtime.
    """
    feeds: list[FeedConfig] = []
    defaults = config.defaults

    for agency in config.agencies:
        if agency.systems:
            # Agency with systems
            for system in agency.systems:
                for feed in system.feeds:
                    feeds.append(
                        _flatten_feed(
                            feed=feed,
                            agency=agency,
                            system=system,
                            defaults=defaults,
                        )
                    )
        elif agency.feeds:
            # Simple agency with direct feeds
            for feed in agency.feeds:
                feeds.append(
                    _flatten_feed(
                        feed=feed,
                        agency=agency,
                        system=None,
                        defaults=defaults,
                    )
                )

    return feeds


async def resolve_feed_secrets(
    feeds: list[FeedConfig],
    project_id: str,
) -> None:
    """Resolve all authentication secrets for feeds.

    Args:
        feeds: List of feed configurations.
        project_id: GCP project ID for Secret Manager.

    Raises:
        SecretManagerError: If any secret cannot be fetched.
    """
    from gtfs_rt_archiver.secrets import resolve_auth_config

    tasks = []
    for feed in feeds:
        if feed.auth is not None:
            tasks.append(resolve_auth_config(feed.auth, project_id))

    if tasks:
        await asyncio.gather(*tasks)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=True,
    )

    # Feed configuration
    config_path: Path = Field(
        default=Path("./agencies.yaml"),
        validation_alias="CONFIG_PATH",
        description="Path to agencies.yaml configuration file",
    )

    # GCS settings
    gcs_bucket: str = Field(
        validation_alias="GCS_BUCKET_RT_PROTOBUF",
        description="Target GCS bucket for archived feeds",
    )

    # GCP settings
    gcp_project_id: str | None = Field(
        default=None,
        validation_alias="GCP_PROJECT_ID",
        description="GCP project ID for Secret Manager (required when feeds have auth)",
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
