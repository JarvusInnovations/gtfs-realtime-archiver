"""Pydantic models for GTFS-RT Archiver configuration."""

from enum import Enum
from typing import Annotated, Self

from pydantic import BaseModel, Field, HttpUrl, model_validator


class FeedType(str, Enum):
    """Types of GTFS-Realtime feeds."""

    VEHICLE_POSITIONS = "vehicle_positions"
    TRIP_UPDATES = "trip_updates"
    SERVICE_ALERTS = "service_alerts"


class AuthType(str, Enum):
    """Type of authentication to apply."""

    HEADER = "header"
    QUERY = "query"


class AuthConfig(BaseModel):
    """Configuration for feed authentication via Secret Manager."""

    type: AuthType
    secret_name: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$")]
    key: str
    value: str | None = None

    # Populated at runtime after secret is fetched (excluded from serialization)
    resolved_value: str | None = Field(default=None, exclude=True)


class RetryConfig(BaseModel):
    """Configuration for retry behavior on transient failures."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_base: float = Field(default=1.0, ge=0.1, le=10.0)
    backoff_max: float = Field(default=10.0, ge=1.0, le=60.0)


class IntervalDefaults(BaseModel):
    """Per-feed-type interval defaults in seconds."""

    vehicle_positions: int = Field(default=20, ge=5, le=3600)
    trip_updates: int = Field(default=20, ge=5, le=3600)
    service_alerts: int = Field(default=60, ge=5, le=3600)

    def get_interval(self, feed_type: FeedType) -> int:
        """Get the default interval for a specific feed type."""
        interval: int = getattr(self, feed_type.value)
        return interval


class DefaultsConfig(BaseModel):
    """Default configuration values applied to all feeds."""

    intervals: IntervalDefaults = Field(default_factory=IntervalDefaults)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    retry: RetryConfig = Field(default_factory=RetryConfig)


class RealtimeFeedConfig(BaseModel):
    """Configuration for a realtime feed within an agency/system (before flattening)."""

    feed_type: FeedType
    url: HttpUrl
    name: str | None = None
    interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    retry: RetryConfig | None = None
    auth: AuthConfig | None = None


class SystemConfig(BaseModel):
    """Configuration for a system within an agency (e.g., SEPTA Bus)."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    name: str
    schedule_url: HttpUrl | None = None
    auth: AuthConfig | None = None
    feeds: list[RealtimeFeedConfig]

    @model_validator(mode="after")
    def validate_has_feeds(self) -> Self:
        """Ensure system has at least one feed."""
        if not self.feeds:
            raise ValueError("System must have at least one feed")
        return self


class AgencyConfig(BaseModel):
    """Configuration for a transit agency."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    name: str
    schedule_url: HttpUrl | None = None
    auth: AuthConfig | None = None
    feeds: list[RealtimeFeedConfig] | None = None
    systems: list[SystemConfig] | None = None

    @model_validator(mode="after")
    def validate_feeds_or_systems(self) -> Self:
        """Ensure agency has either feeds or systems, but not both."""
        has_feeds = self.feeds is not None and len(self.feeds) > 0
        has_systems = self.systems is not None and len(self.systems) > 0

        if has_feeds and has_systems:
            raise ValueError("Agency cannot have both feeds and systems")
        if not has_feeds and not has_systems:
            raise ValueError("Agency must have either feeds or systems")
        return self


class AgenciesFileConfig(BaseModel):
    """Schema for the agencies.yaml configuration file."""

    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    agencies: list[AgencyConfig]


class FeedConfig(BaseModel):
    """Configuration for a single GTFS-RT feed (flattened for runtime)."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    name: str
    url: HttpUrl
    feed_type: FeedType

    # Agency/system context
    agency_id: str
    agency_name: str
    system_id: str | None = None
    system_name: str | None = None
    schedule_url: HttpUrl | None = None

    # Runtime settings
    interval_seconds: int = Field(default=20, ge=5, le=3600)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    auth: AuthConfig | None = None
