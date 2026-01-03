"""Pydantic models for GTFS-RT Archiver configuration."""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl


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
    value: str = "${SECRET}"

    # Populated at runtime after secret is fetched (excluded from serialization)
    resolved_value: str | None = Field(default=None, exclude=True)


class RetryConfig(BaseModel):
    """Configuration for retry behavior on transient failures."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_base: float = Field(default=1.0, ge=0.1, le=10.0)
    backoff_max: float = Field(default=10.0, ge=1.0, le=60.0)


class FeedConfig(BaseModel):
    """Configuration for a single GTFS-RT feed."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    name: str
    url: HttpUrl
    feed_type: FeedType
    agency: str | None = None
    interval_seconds: int = Field(default=20, ge=5, le=3600)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    auth: AuthConfig | None = None


class DefaultsConfig(BaseModel):
    """Default configuration values applied to all feeds."""

    interval_seconds: int = Field(default=20, ge=5, le=3600)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    retry: RetryConfig = Field(default_factory=RetryConfig)


class FeedsFileConfig(BaseModel):
    """Schema for the feeds.yaml configuration file."""

    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    feeds: list[FeedConfig]
