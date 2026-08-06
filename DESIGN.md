# GTFS-RT Archiver Design Document

## Overview

A lightweight, resilient service for archiving GTFS-Realtime feeds to cloud storage at configurable intervals. Designed to handle hundreds of feeds cheaply and reliably, deployable to Google Cloud Run or Kubernetes.

## Background

### Problem Statement

Existing GTFS-RT archiver implementations suffer from:

1. **Unnecessary complexity**: Multi-component architectures (Ticker → Redis → Consumer) introduce operational overhead and failure modes
2. **Redis connection instability**: Consumers lose Redis connections, stop processing, and require manual intervention
3. **Misaligned architecture**: Redis provides durability and work distribution, but GTFS-RT archiving needs neither—missed ticks are useless to retry

### Design Goals

| Goal | Description |
| ------ | ------------- |
| **Simplicity** | Single container deployment, minimal moving parts |
| **Resilience** | Graceful handling of network failures, feed outages, and transient errors |
| **Efficiency** | Handle 500+ feeds with <1GB memory using async I/O |
| **Observability** | Prometheus metrics, structured logging, health endpoints |
| **Configurability** | Per-feed intervals, global defaults, runtime configuration |
| **Cost-effective** | Run on Cloud Run with minimal always-on resources |

### Non-Goals

- Retry missed fetches (stale data has no value)
- Persist fetch queue (in-memory scheduling is sufficient)
- Parse or validate GTFS-RT content (raw archival only)
- Support non-HTTP feed sources

---

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────────┐
│                     GTFS-RT Archiver Container                      │
├─────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                      Python Async Runtime                      │ │
│  │                                                                │ │
│  │  ┌─────────────────┐     ┌───────────────────────────────────┐ │ │
│  │  │   APScheduler   │     │         Fetch Worker Pool         │ │ │
│  │  │ • Per-feed jobs │────▶│ • httpx.AsyncClient               │ │ │
│  │  │ • Cron triggers │     │ • Semaphore(max_concurrent)       │ │ │
│  │  │ • Misfire grace │     │ • Retry with exponential backoff  │ │ │
│  │  └─────────────────┘     └───────────────────────────────────┘ │ │
│  │                                         │                      │ │
│  │                                         ▼                      │ │
│  │                          ┌───────────────────────────────────┐ │ │
│  │                          │       Storage Writer              │ │ │
│  │                          │ • gcloud-aio-storage (async)      │ │ │
│  │                          │ • Hive-partitioned paths          │ │ │
│  │                          └───────────────────────────────────┘ │ │
│  │                                                                │ │
│  │  ┌───────────────────────────────────────────────────────────┐ │ │
│  │  │       Health + Metrics Server (port 8080)                 │ │ │
│  │  │       /health  •  /ready  •  /metrics                     │ │ │
│  │  └───────────────────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                      ┌──────────────────────────┐
                      │  GCS: protobuf.gtfsrt.io │
                      │  (raw protobuf archives) │
                      └──────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────┐
│                     Dagster Pipeline                               │
├────────────────────────────────────────────────────────────────────┤
│  Daily Compaction (2am UTC)                                        │
│  ┌─────────────────┐     ┌───────────────────────────────────────┐ │
│  │  Feed Discovery │────▶│  Streaming Parquet Writer             │ │
│  │  (scan GCS)     │     │  • Parse protobuf → PyArrow tables    │ │
│  └─────────────────┘     │  • Batch writes (memory efficient)    │ │
│                          │  • zstd compression                   │ │
│                          └───────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                      ┌──────────────────────────┐
                      │  GCS: parquet.gtfsrt.io  │
                      │  (compacted parquet)     │
                      └──────────────────────────┘
```

### Component Responsibilities

#### Scheduler (APScheduler 4.x)

- Maintains per-feed job schedules with configurable intervals
- Uses `misfire_grace_time` to drop stale jobs (default: 5 seconds)
- Limits concurrent job instances per feed to 1 (prevents overlap)
- Runs entirely in-memory (no job store persistence needed)

#### Fetch Worker Pool

- Async HTTP client (httpx) with connection pooling
- Semaphore-based concurrency limiting (default: 100 concurrent fetches)
- Per-request timeout (default: 30 seconds)
- Retry logic with exponential backoff for transient errors only

#### Storage Writer

- Async GCS uploads via gcloud-aio-storage
- Hive-style partitioned paths for query efficiency
- Stores raw response bytes (protobuf) without parsing
- Optional metadata sidecar files (headers, timing)

#### Health/Metrics Server

A single aiohttp server on `HEALTH_PORT` (default 8080) serves both concerns:

- `/health` for liveness probes — returns scheduler state and active job count
- `/ready` for readiness probes
- `/metrics` for Prometheus scraping — fetch duration, success/error counts, active feeds
- Per-feed labels for granular observability
- Used by Cloud Run and Kubernetes for health checks

---

## Data Model

### Feed Configuration

`agencies.yaml` is a nested hierarchy: `agencies` contain either `feeds` directly, or `systems` that contain `feeds` (an agency cannot have both). Feed IDs are not written in the file — they are generated during flattening as `{agency-id}[-{system-id}]-{feed-type}` (e.g., `septa-bus-vehicle-positions`, `bart-trip-updates`).

```yaml
# agencies.yaml
defaults:
  timeout_seconds: 30
  retry:
    max_attempts: 3
    backoff_base: 1.0
    backoff_max: 10.0
  intervals:                      # Per-feed-type interval defaults
    vehicle_positions: 20
    trip_updates: 20
    service_alerts: 60

agencies:
  # Simple agency with direct feeds
  - id: bart
    name: BART
    auth:                         # Agency-level auth inherited by all feeds
      type: query                 # Auth via query parameter
      secret_name: bart-api-key   # Secret name in GCP Secret Manager
      key: key                    # Query parameter name
      # value field is optional - uses entire secret directly when omitted
    feeds:
      - feed_type: trip_updates
        url: https://api.bart.gov/gtfsrt/tripupdate.aspx
        interval_seconds: 15      # Override the feed-type default

  # Agency with multiple systems (e.g., bus vs rail)
  - id: septa
    name: SEPTA
    systems:
      - id: bus
        name: Bus
        schedule_url: https://www3.septa.org/developer/google_bus.zip
        feeds:
          - feed_type: vehicle_positions
            url: https://www3.septa.org/gtfsrt/septa-pa-us/Vehicle/rtVehiclePosition.pb
          - feed_type: trip_updates
            url: https://www3.septa.org/gtfsrt/septa-pa-us/Trip/rtTripUpdates.pb
      - id: rail
        name: Regional Rail
        schedule_url: https://www3.septa.org/developer/google_rail.zip
        feeds:
          - feed_type: vehicle_positions
            url: https://www3.septa.org/gtfsrt/septarail-pa-us/Vehicle/rtVehiclePosition.pb
```

At startup, `config.flatten_agencies()` flattens the hierarchy into a list of runtime `FeedConfig` objects, resolving inheritance:

- **Auth**: feed > system > agency
- **Interval**: feed `interval_seconds` > per-feed-type default (`defaults.intervals`)
- **Timeout / retry**: feed > global default
- **Schedule URLs**: system > agency

### Pydantic Models

Defined in `src/gtfs_rt_archiver/models.py`. The file schema (`AgenciesFileConfig` → `AgencyConfig` → `SystemConfig` → `RealtimeFeedConfig`) mirrors the YAML above; `FeedConfig` is the flattened runtime shape produced by `config.flatten_agencies()`.

```python
from pydantic import BaseModel, Field, HttpUrl
from typing import Annotated
from enum import Enum

class FeedType(str, Enum):
    VEHICLE_POSITIONS = "vehicle_positions"
    TRIP_UPDATES = "trip_updates"
    SERVICE_ALERTS = "service_alerts"

class AuthType(str, Enum):
    HEADER = "header"
    QUERY = "query"

class AuthConfig(BaseModel):
    type: AuthType
    secret_name: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$")]
    key: str
    value: str | None = None  # Optional: template with ${SECRET} placeholder
    resolved_value: str | None = Field(default=None, exclude=True)

class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_base: float = Field(default=1.0, ge=0.1, le=10.0)
    backoff_max: float = Field(default=10.0, ge=1.0, le=60.0)

class IntervalDefaults(BaseModel):
    vehicle_positions: int = Field(default=20, ge=5, le=3600)
    trip_updates: int = Field(default=20, ge=5, le=3600)
    service_alerts: int = Field(default=60, ge=5, le=3600)

class DefaultsConfig(BaseModel):
    intervals: IntervalDefaults = Field(default_factory=IntervalDefaults)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    retry: RetryConfig = Field(default_factory=RetryConfig)

class RealtimeFeedConfig(BaseModel):
    """A feed as written in agencies.yaml (before flattening)."""
    feed_type: FeedType
    url: HttpUrl
    name: str | None = None
    interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    retry: RetryConfig | None = None
    auth: AuthConfig | None = None

class SystemConfig(BaseModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    name: str
    schedule_url: HttpUrl | None = None
    schedule_urls: list[HttpUrl] | None = None
    auth: AuthConfig | None = None
    feeds: list[RealtimeFeedConfig]

class AgencyConfig(BaseModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    name: str
    schedule_url: HttpUrl | None = None
    schedule_urls: list[HttpUrl] | None = None
    auth: AuthConfig | None = None
    feeds: list[RealtimeFeedConfig] | None = None   # Either direct feeds...
    systems: list[SystemConfig] | None = None       # ...or systems (not both)

class AgenciesFileConfig(BaseModel):
    """Top-level schema for agencies.yaml."""
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    agencies: list[AgencyConfig]

class FeedConfig(BaseModel):
    """A single feed, flattened for runtime by config.flatten_agencies()."""
    id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]  # {agency}[-{system}]-{feed-type}
    name: str
    url: HttpUrl
    feed_type: FeedType
    agency_id: str
    agency_name: str
    system_id: str | None = None
    system_name: str | None = None
    schedule_url: HttpUrl | None = None                 # Primary (first) schedule URL
    schedule_urls: list[HttpUrl] = Field(default_factory=list)
    interval_seconds: int = Field(default=20, ge=5, le=3600)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    auth: AuthConfig | None = None
```

### Storage Path Structure

**Protobuf Archives** (raw snapshots from archiver):

```
gs://protobuf.gtfsrt.io/
└── {feed_type}/
    └── date={YYYY-MM-DD}/
        └── hour={YYYY-MM-DDTHH:00:00Z}/
            └── base64url={encoded-url}/
                ├── {ISO8601_timestamp}.pb      # Raw protobuf
                └── {ISO8601_timestamp}.meta    # Optional metadata JSON
```

**Parquet Files** (compacted daily by Dagster):

```
gs://parquet.gtfsrt.io/
└── {feed_type}/
    └── date={YYYY-MM-DD}/
        └── base64url={encoded-url}/
            └── data.parquet                    # All records for the day
```

The `base64url` partition contains the URL-safe base64 encoding of the base feed URL (without auth query parameters), without padding characters. This ensures consistent storage paths across secret rotations and prevents secret leakage in storage paths.

Example (protobuf):

```
gs://protobuf.gtfsrt.io/
└── vehicle_positions/
    └── date=2025-01-15/
        └── hour=2025-01-15T14:00:00Z/
            └── base64url=aHR0cHM6Ly93d3czLnNlcHRhLm9yZy9ndGZzcnQvc2VwdGEtcGEtdXMvVmVoaWNsZS9ydFZlaGljbGVQb3NpdGlvbi5wYg/
                ├── 2025-01-15T14:20:00.000Z.pb
                ├── 2025-01-15T14:20:00.000Z.meta
                ├── 2025-01-15T14:20:20.000Z.pb
                └── 2025-01-15T14:20:20.000Z.meta
```

Example (parquet):

```
gs://parquet.gtfsrt.io/
└── vehicle_positions/
    └── date=2025-01-15/
        └── base64url=aHR0cHM6Ly93d3czLnNlcHRhLm9yZy9ndGZzcnQvc2VwdGEtcGEtdXMvVmVoaWNsZS9ydFZlaGljbGVQb3NpdGlvbi5wYg/
            └── data.parquet
```

### Metadata File Format

```json
{
  "feed_id": "septa-vehicle-positions",
  "url": "https://www3.septa.org/gtfsrt/...",
  "fetch_timestamp": "2025-01-15T14:20:00.123Z",
  "duration_ms": 245,
  "response_code": 200,
  "content_length": 15234,
  "content_type": "application/x-protobuf",
  "headers": {
    "etag": "\"abc123\"",
    "last-modified": "Wed, 15 Jan 2025 14:19:58 GMT"
  }
}
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
| ---------- | ------------- | --------- |
| `CONFIG_PATH` | Path to agencies.yaml | `./agencies.yaml` |
| `GCS_BUCKET_RT_PROTOBUF` | Target GCS bucket for protobuf archives | Required |
| `GCS_BUCKET_RT_PARQUET` | Target GCS bucket for compacted parquet files | Required (Dagster) |
| `GCP_PROJECT_ID` | GCP project ID for Secret Manager | Required if auth used |
| `MAX_CONCURRENT` | Max concurrent fetches | `100` |
| `HEALTH_PORT` | Health check and metrics server port | `8080` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FORMAT` | `json` or `text` | `json` |
| `SHARD_INDEX` | Index of this shard (0-based) | `0` |
| `TOTAL_SHARDS` | Total number of shards | `1` |
| `DAGSTER_HOME` | Dagster home directory (absolute path) | Required (Dagster) |
| `STORAGE_EMULATOR_HOST` | Fake GCS server URL for local dev | - |

### Secret Manager Integration

Feed authentication secrets are fetched from GCP Secret Manager at startup:

```yaml
auth:
  type: header                # header or query
  secret_name: mta-api-key    # Secret name in GCP Secret Manager
  key: x-api-key              # Header name or query param name
  value: "${SECRET}"          # Optional: template with ${SECRET} placeholder
                              # When omitted, uses entire secret directly
```

The `value` field is optional:

- **When omitted**: The entire secret value is used directly as the authentication value
- **When provided**: The `${SECRET}` placeholder is replaced with the secret value (e.g., `"Bearer ${SECRET}"`)

The `GCP_PROJECT_ID` environment variable must be set when feeds have auth configured.

**IAM Access Control:**
Secrets must be tagged with `type=feed-key` for the service account to access them. The Terraform configuration creates the tag key/value and sets up IAM conditions.

---

## Error Handling

### Retry Strategy

```python
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

RETRYABLE_EXCEPTIONS = (
    httpx.TransportError,      # Connection errors
    httpx.TimeoutException,    # Timeouts
)

NON_RETRYABLE_STATUS_CODES = {
    400,  # Bad request (our fault)
    401,  # Unauthorized (config issue)
    403,  # Forbidden (config issue)
    404,  # Not found (URL changed)
    410,  # Gone (feed discontinued)
}

@retry(
    stop=stop_after_attempt(config.retry.max_attempts),
    wait=wait_exponential(
        multiplier=config.retry.backoff_base,
        max=config.retry.backoff_max,
    ),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
)
async def fetch_feed(client: httpx.AsyncClient, feed: FeedConfig) -> FetchResult:
    response = await client.get(
        str(feed.url),
        params=feed.query_params,
        headers=feed.headers,
        timeout=feed.timeout_seconds,
    )

    if response.status_code in NON_RETRYABLE_STATUS_CODES:
        # Log and skip, don't retry
        raise NonRetryableError(response.status_code)

    response.raise_for_status()
    return FetchResult(content=response.content, headers=dict(response.headers))
```

### Failure Categories

| Category | Behavior | Example |
| ---------- | ---------- | --------- |
| **Transient network** | Retry with backoff | Connection reset, DNS timeout |
| **Slow response** | Retry with backoff | Request timeout |
| **Auth failure** | Log error, skip feed | 401/403 response |
| **Feed gone** | Log error, skip feed | 404/410 response |
| **Server error** | Retry with backoff | 500/502/503 response |
| **Storage failure** | Retry with backoff | GCS upload failed |

### Graceful Degradation

- Individual feed failures don't affect other feeds
- Failed fetches are logged with full context
- Metrics track failure rates per feed
- No circuit breaker (feeds are independent)

---

## Observability

### Prometheus Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

# Counters
fetch_total = Counter(
    "gtfs_rt_fetch_total",
    "Total fetch attempts",
    ["feed_id", "feed_type", "agency"],
)
fetch_success = Counter(
    "gtfs_rt_fetch_success_total",
    "Successful fetches",
    ["feed_id", "feed_type", "agency"],
)
fetch_errors = Counter(
    "gtfs_rt_fetch_errors_total",
    "Failed fetches",
    ["feed_id", "feed_type", "agency", "error_type"],
)
upload_success = Counter(
    "gtfs_rt_upload_success_total",
    "Successful GCS uploads",
    ["feed_id", "feed_type", "agency"],
)
upload_errors = Counter(
    "gtfs_rt_upload_errors_total",
    "Failed GCS uploads",
    ["feed_id", "feed_type", "agency"],
)

# Histograms
fetch_duration = Histogram(
    "gtfs_rt_fetch_duration_seconds",
    "Time to fetch feed",
    ["feed_id", "feed_type", "agency"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)
upload_duration = Histogram(
    "gtfs_rt_upload_duration_seconds",
    "Time to upload to GCS",
    ["feed_id", "feed_type", "agency"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
fetch_bytes = Histogram(
    "gtfs_rt_fetch_bytes",
    "Response size in bytes",
    ["feed_id", "feed_type", "agency"],
    buckets=[1000, 10000, 50000, 100000, 500000, 1000000],
)

# Gauges
active_feeds = Gauge(
    "gtfs_rt_active_feeds",
    "Number of configured feeds",
)
scheduler_jobs = Gauge(
    "gtfs_rt_scheduler_jobs",
    "Number of scheduled jobs",
)
last_fetch_timestamp = Gauge(
    "gtfs_rt_last_fetch_timestamp",
    "Unix timestamp of last fetch attempt",
    ["feed_id"],
)
```

### Structured Logging

```python
import structlog

logger = structlog.get_logger()

# Fetch success
logger.info(
    "fetch_success",
    feed_id=feed.id,
    feed_type=feed.feed_type,
    url=str(feed.url),
    duration_ms=elapsed_ms,
    response_code=200,
    content_length=len(content),
)

# Fetch error
logger.error(
    "fetch_error",
    feed_id=feed.id,
    feed_type=feed.feed_type,
    url=str(feed.url),
    error_type=type(exc).__name__,
    error_message=str(exc),
    attempt=attempt_number,
)
```

### Health Check Response

```json
GET /health

{
  "version": "dev",
  "status": "healthy",
  "uptime_seconds": 3600.0,
  "scheduler": {
    "running": true,
    "jobs_scheduled": 45
  },
  "feeds": {
    "total": 45
  }
}
```

---

## Deployment

### Cloud Run Configuration

```hcl
# tf/main.tf (OpenTofu)

resource "google_cloud_run_v2_service" "archiver" {
  name     = "gtfs-rt-archiver"
  location = var.region

  template {
    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle = false  # Keep CPU allocated for scheduler
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "GCS_BUCKET_RT_PROTOBUF"
        value = google_storage_bucket.protobuf.name
      }
      env {
        name  = "GCS_BUCKET_RT_PARQUET"
        value = google_storage_bucket.parquet.name
      }
      env {
        name  = "LOG_FORMAT"
        value = "json"
      }

      # Mount secrets
      env {
        name = "BART_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.bart_api_key.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 3
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        period_seconds    = 30
        failure_threshold = 3
      }
    }

    scaling {
      min_instance_count = 1  # Always-on for scheduler
      max_instance_count = 1  # Single instance (no sharding yet)
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}
```

### Scaling Strategy

| Feed Count | Instances | Configuration |
| ------------ | ----------- | --------------- |
| 1-100 | 1 | Single instance, 100 concurrent |
| 100-300 | 1-2 | Increase max_concurrent or add instance |
| 300-500 | 2-3 | Shard feeds across instances |
| 500+ | 3+ | Hash-based sharding |

#### Sharding Implementation

```python
# When SHARD_INDEX and TOTAL_SHARDS are set (see scheduler.py)
def should_handle_feed(feed: FeedConfig, shard_index: int, total_shards: int) -> bool:
    if total_shards <= 1:
        return True
    # MD5 for deterministic hashing across processes (Python's hash() is randomized)
    feed_hash = int(hashlib.md5(feed.id.encode()).hexdigest(), 16)
    return feed_hash % total_shards == shard_index

active_feeds = [f for f in all_feeds if should_handle_feed(f, shard_index, total_shards)]
```

---

## Dagster Compaction Pipeline

The Dagster pipeline compacts raw protobuf archives into daily Parquet files for efficient analytics queries.

### Design Decisions

| Decision | Rationale |
| ---------- | ----------- |
| **Daily partitions** | Medium feed count (~100s) makes daily batches practical without excessive memory usage |
| **Runtime feed discovery** | Scan GCS for `base64url=` directories instead of maintaining a feed registry |
| **Streaming parquet writer** | Process feeds in batches to limit memory usage (vs. accumulating all records) |
| **Denormalization** | Flatten nested GTFS-RT structures for SQL-friendly analytics |

**On the denormalized grain (recorded 2026-08-05, retroactively):** the
one-row-per-innermost-repeated-element grain (trip_updates rows are
stop_time_updates, service_alerts rows are informed_entities) was set in the
original compaction commit (e4a11e1) **without articulated rationale** — the
alternative of one row per feed-entity message, with unnesting handled
downstream in a transform layer, was never weighed. TripModifications
entities existed in the spec at that time and were seemingly not
supported or considered by the decision. The tradeoff that choice bought is
now visible: a producer's single feed message splits across multiple tables
(a TripUpdate's trip-level fields replicate across its STU rows, and the
TripModifications family lands in separate tables), and table schemas are
fixed at compaction time — where changes require re-materialization — rather
than in a downstream transform layer (dbt) where changes are cheap. The
grain is **retained as-is** for the original three tables because changing
it now would be disruptive to every existing consumer and partition; tables
added later (trip_modifications, shapes, stops) use the entity/message grain
with repeated structures JSON-encoded instead.

### Assets

| Asset | Description | Grain |
| ------- | ------------- | ------- |
| `vehicle_positions_parquet` | Vehicle positions for a day | One row per vehicle position update |
| `trip_updates_parquet` | Trip updates for a day | One row per stop_time_update (or base record if none) |
| `service_alerts_parquet` | Service alerts for a day | One row per informed_entity (or base record if none) |
| `trip_modifications_parquet` | TripModifications (detour) entities for a day (#95) | One row per entity; repeated structures JSON-encoded |
| `shapes_parquet` | Shape (detour geometry) entities for a day (#96) | One row per entity |
| `stops_parquet` | Ad-hoc/replacement Stop entities for a day (#97) | One row per entity |

`trip_updates_parquet`, `trip_modifications_parquet`, `shapes_parquet`, and
`stops_parquet` are the four outputs of ONE non-subsettable `@multi_asset`:
Madison Metro and Big Blue Bus publish trip_modifications/shape/stop entities
inside their trip_updates feed URLs (2026-08-04 census), so all four tables
are extracted from a single download+parse pass of the trip_updates raw
files. Re-materializing the partition rewrites all four outputs (same source
bytes); an output with zero records uploads nothing.

### Data Flow

```
1. Discover feeds: List base64url= directories in GCS for the partition date
2. For each feed:
   a. List all .pb files in hour=* subdirectories
   b. Parse protobuf → extract records
   c. Write batch to streaming ParquetWriter
3. Upload final parquet to output bucket
```

### PyArrow Schemas

Each feed type has a defined schema for consistent output:

**Vehicle Positions:**

- `source_file`, `feed_url`, `feed_timestamp`, `fetch_timestamp`, `entity_id`
- `trip_id`, `route_id`, `direction_id`, `start_date`, `start_time`, `schedule_relationship`
- `vehicle_id`, `vehicle_label`, `license_plate`, `wheelchair_accessible`
- `latitude`, `longitude`, `bearing`, `odometer`, `speed`
- `current_stop_sequence`, `stop_id`, `current_status`, `timestamp`, `congestion_level`, `occupancy_status`, `occupancy_percentage`
- Modified trip: `modified_trip_modifications_id`, `modified_trip_affected_trip_id`, `modified_trip_start_date`, `modified_trip_start_time`
- Carriages: `multi_carriage_details_json` (repeated CarriageDetails, JSON-encoded)
- Header/entity: `feed_version`, `incrementality`, `is_deleted`

**Trip Updates:**

- Base: `source_file`, `feed_url`, `feed_timestamp`, `fetch_timestamp`, `entity_id`
- Trip: `trip_id`, `route_id`, `direction_id`, `start_date`, `start_time`, `schedule_relationship`
- Vehicle: `vehicle_id`, `vehicle_label`, `license_plate`, `wheelchair_accessible`
- Update: `trip_delay`, `trip_timestamp`
- Trip properties: `trip_properties_trip_id`, `trip_properties_start_date`, `trip_properties_start_time`, `trip_properties_shape_id`, `trip_properties_trip_headsign`, `trip_properties_trip_short_name`
- Modified trip: `modified_trip_modifications_id`, `modified_trip_affected_trip_id`, `modified_trip_start_date`, `modified_trip_start_time`
- Stop time: `stop_sequence`, `stop_id`, `arrival_delay`, `arrival_time`, `arrival_uncertainty`, `arrival_scheduled_time`, `departure_delay`, `departure_time`, `departure_uncertainty`, `departure_scheduled_time`, `departure_occupancy_status`, `stop_schedule_relationship`, `assigned_stop_id`, `stop_headsign`, `pickup_type`, `drop_off_type`
- Header/entity: `feed_version`, `incrementality`, `is_deleted`

**Service Alerts:**

- Base: `source_file`, `feed_url`, `feed_timestamp`, `fetch_timestamp`, `entity_id`
- Alert: `cause`, `effect`, `severity_level`, `cause_detail`, `effect_detail`, `url`, `header_text`, `description_text`, `tts_header_text`, `tts_description_text`, `image_url`, `image_media_type`, `image_alternative_text`
- Active period: `active_period_start`, `active_period_end` (first period **as published** — the spec doesn't require chronological order, so use `active_periods_json` when ordering matters), `active_periods_json` (full list)
- Informed entity: `agency_id`, `route_id`, `route_type`, `stop_id`, `direction_id`, `trip_id`, `trip_route_id`, `trip_direction_id`, `trip_start_time`, `trip_start_date`, `trip_schedule_relationship`, `trip_modified_trip_modifications_id`, `trip_modified_trip_affected_trip_id`, `trip_modified_trip_start_date`, `trip_modified_trip_start_time`
- Communication/impact periods: `communication_periods_json`, `impact_periods_json` (same encoding and NULL semantics as `active_periods_json`)
- Translations (#98): `header_text_translations_json`, `description_text_translations_json`, `url_translations_json`, `tts_header_text_translations_json`, `tts_description_text_translations_json`, `cause_detail_translations_json`, `effect_detail_translations_json`, `image_alternative_text_translations_json` (each `[{"text","language"},…]` in publisher order, NULL when unset), `image_localized_images_json` (`[{"url","media_type","language"},…]`)
- Header/entity: `feed_version`, `incrementality`, `is_deleted`

**Trip Modifications** (#95):

- Base: `source_file`, `feed_url`, `feed_timestamp`, `fetch_timestamp`, `entity_id`
- Payload: `selected_trips_json`, `start_times_json`, `service_dates_json`, `modifications_json`
- Header/entity: `feed_version`, `incrementality`, `is_deleted`

**Shapes** (#96):

- Base: `source_file`, `feed_url`, `feed_timestamp`, `fetch_timestamp`, `entity_id`
- Payload: `shape_id`, `encoded_polyline` (Google encoded polyline)
- Header/entity: `feed_version`, `incrementality`, `is_deleted`

**Stops** (#97):

- Base: `source_file`, `feed_url`, `feed_timestamp`, `fetch_timestamp`, `entity_id`
- Payload: `stop_id`, `stop_code_translations_json`, `stop_name_translations_json`, `tts_stop_name_translations_json`, `stop_desc_translations_json`, `stop_lat`, `stop_lon`, `zone_id`, `stop_url_translations_json`, `parent_station`, `stop_timezone`, `wheelchair_boarding`, `level_id`, `platform_code_translations_json`
- Header/entity: `feed_version`, `incrementality`, `is_deleted`

The three entity-grain tables (trip_modifications, shapes, stops — the
TripModifications detour family, experimental in the spec) follow different
conventions than the original three, deliberately (see the grain-decision
entry above): one row per FeedEntity per snapshot, with every repeated
structure JSON-encoded whole so unnesting is a downstream transform concern.
JSON columns are NULL when the repeated field is empty (never `"[]"`), use
per-field presence inside objects (unset → JSON null), and keep nested empty
lists as `[]` (the parent exists, its list is empty). Join keys:
`trip_modifications.entity_id` is what `modified_trip_modifications_id`
(trip_updates/vehicle_positions rows) points at;
`Modification.service_alert_id` inside `modifications_json` references
service_alerts entity ids; `selected_trips.shape_id` and
`trip_properties_shape_id` reference `shapes.shape_id`. The stops table's
`*_translations_json` columns capture ALL translations as
`[{"text": …, "language": …}, …]` in publisher order — full fidelity from
day one, unlike the service_alerts keep-first columns (#98); any display
selection rule is derivable downstream. Duplication is accepted by design:
polylines and stop definitions repeat identically in every ~20s snapshot
for a detour's lifetime (the VP/TU every-snapshot model; zstd + dictionary
encoding collapse repeats on disk) — dedup at query time, e.g.
`QUALIFY ROW_NUMBER() OVER (PARTITION BY shape_id ORDER BY feed_timestamp DESC) = 1`.

Service_alerts scalar translated columns (`header_text` et al.) store the
**first translation as published** — which is producer whim, not guaranteed
English (AC Transit lists Spanish first; MTA publishes `en` and `en-html`
variants). From #98 they sit alongside full-fidelity
`*_translations_json` companions capturing every translation with its
language tag, so nothing is lost and any display-selection rule
(prefer-`en`, skip `-html`) is a downstream transform concern, not a
compaction decision. The stops table's translated fields ship as
`*_translations_json` only. All columns added by #91 (including the
gtfs-realtime-bindings-2.2.0-gated `*_scheduled_time`, `cause_detail`,
`effect_detail`, `image_url`, `modified_trip_*`) populate only from the
release that shipped them onward; earlier partitions lack the columns and
read as NULL from the BigQuery external tables (DuckDB consumers should use
`union_by_name`). Note that **re-materializing any old partition whose raw
`.pb` files are still within the 365-day retention window backfills its new
columns** (compaction rewrites partitions wholesale from raw), so column
presence varies partition-to-partition with re-run history — another reason
for `union_by_name`. Past that window the remedy silently no-ops: a re-run
over expired raw data returns success with zero records and leaves the
existing parquet untouched (deliberate — a lifecycle expiry must not destroy
derived data). Within service_alerts, bare
informed-entity column names (`agency_id`, `route_id`, `stop_id`,
`direction_id`) carry EntitySelector semantics — distinct from the
trip-descriptor meanings the same names have in vehicle_positions/
trip_updates. Similarly, in trip_updates the `StopTimeProperties` fields
(`assigned_stop_id`, `stop_headsign`, `pickup_type`, `drop_off_type`) are
deliberately bare — each denormalized row already *is* a stop_time_update, so
STU-level fields take row-level names, and only fields hoisted from
trip-level nested messages (`trip_properties_*`, `modified_trip_*`) carry a
provenance prefix. Note `pickup_type`/`drop_off_type`/`stop_headsign` also
name GTFS **static** `stop_times.txt` columns; qualify columns when joining
static and RT tables. The proto messages shared by vehicle_positions and
trip_updates (`VehicleDescriptor`, `TripDescriptor`) are captured
symmetrically — `license_plate`, `wheelchair_accessible`, and
`modified_trip_*` appear in both tables — so columnset differences between
the two reflect feed-type-specific messages only.

String-presence semantics: fields of sparse-by-design nested messages
(`trip_properties_*`, `modified_trip_*`, `assigned_stop_id`, `stop_headsign`)
use per-field presence — unset is NULL, never `""`. Strings on
routinely-populated parents (`vehicle_id`, `vehicle_label`, trip-descriptor
strings) keep the long-standing parent-presence convention, where an unset
field on a present parent reads as `""`. One migrated
convention: `license_plate` uses per-field presence (unset → NULL, never `""`)
in **both** feed types from v0.9.3 onward — plates are rarely published, and
the old parent-presence convention read `""` on nearly every row. The
vehicle_positions column predates the switch, so **partitions materialized
before v0.9.3 contain `""`** for a present-descriptor/unset-plate row; this
historical inconsistency is deliberate (accepted on PR #94 in preference to
carrying a permanent cross-table asymmetry). Reads spanning the boundary
should normalize with `NULLIF(license_plate, '')`; re-materializing an old
partition (or a #91 backfill) rewrites it under the new convention — while
its raw `.pb` files remain within the 365-day retention window (see above).
`feed_timestamp` (all three tables) made the same v0.9.3 migration in the
other value domain: it moved from truthiness to per-field presence, so an
explicitly-published `header.timestamp = 0` now records `0` where it
previously recorded NULL — pathological in practice (a 1970 timestamp),
noted for completeness, same per-partition re-run boundary as
`license_plate`.

Enum-presence semantics: enums added by #91/#94 (`wheelchair_accessible`,
`pickup_type`, `drop_off_type`, `departure_occupancy_status`,
`incrementality`, `service_alerts.trip_schedule_relationship`) and the
pre-existing per-field enums (`stop_schedule_relationship`, `cause`,
`effect`, `severity_level`, `current_status`, `congestion_level`,
`occupancy_status`) — an exhaustive list — use per-field presence — unset is
NULL, and an explicitly-set 0 is captured as 0. The exception is exactly two
columns: `vehicle_positions.schedule_relationship` and
`trip_updates.schedule_relationship`. They predate the convention and
materialize the proto default, so unset reads `0` (= SCHEDULED, which is
what the spec says unset means) rather than NULL. Note the resulting split
on the *same proto field*: `service_alerts.trip_schedule_relationship` is
the identical `TripDescriptor.schedule_relationship`, captured later under
the per-field convention — unset reads NULL there but `0` in VP/TU.
Normalize with `COALESCE(trip_schedule_relationship, 0)` when unioning trip
descriptors across tables. `pickup_type IS NULL` and
`schedule_relationship = 0` therefore both encode "the producer did not
say" — in adjacent columns of the same row.

Complete-capture policy (PR #94): every leaf field of the three archived
entity types maps to a column or carries a recorded drop reason, enforced by
a descriptor-walk manifest test — so adding a feed never requires a field
audit, and a bindings bump that adds fields fails CI until dispositioned.
Scope caveat: this covers the **base schema** only. Every GTFS-RT message
also declares proto2 extension ranges, and producer extensions (e.g.
MTA-NYCT's `nyct_subway.proto` train/track fields) arrive as unknown fields
that compaction drops — they survive only in the raw `.pb` archive within
its 365-day retention (#101).
Header/entity columns: `feed_version` (free-form producer version;
unpopulated fleet-wide as of the 2026-08-04 census), `incrementality`
(per-field presence — explicit FULL_DATASET reads 0, unset NULL; the
extractors assume FULL_DATASET semantics, so a non-zero value here is the
signal that a DIFFERENTIAL feed appeared), and `is_deleted` (captured on
payload-bearing entities — MTA sets it explicitly false; bare deletion
tombstones carry no payload and yield no row, which only matters for
DIFFERENTIAL feeds). Repeated messages ride as JSON columns:
`multi_carriage_details_json`, `communication_periods_json`,
`impact_periods_json` (both period columns share `active_periods_json`'s
encoding, NULL semantics, and query recipe). Feeds can also carry entity
types outside these three tables entirely — the 2026-08-04 census found
Madison Metro and Big Blue Bus publishing `shape`, `stop`, and
`trip_modifications` entities, which compaction skips; capturing them means
new tables (tracked on #91).

`active_periods_json` is NULL when an alert declares no active periods
(spec: always active); `"[]"` is never emitted. It is a STRING column
(BigQuery's native JSON type is unavailable for Parquet external tables).
The "active at time T" recipe has **two** load-bearing NULL rules: a NULL
*column* means always active (a plain `UNNEST`/comma join yields zero rows
for it, silently reporting the alert inactive — wrap the period check in
`EXISTS`), and a JSON-null *start/end* means unbounded on that side
(`JSON_VALUE` returns SQL NULL for it, and NULL comparisons filter the
row). Both recipes use `EXISTS` so each alert row is returned **at most
once** — a `LEFT JOIN UNNEST ... ON TRUE` form fans out one row per
matching period (overlapping periods are legal and observed), silently
inflating any aggregation over the result. BigQuery:

```sql
FROM gtfs_rt.service_alerts a
WHERE a.active_periods_json IS NULL
   OR EXISTS (
        SELECT 1
        FROM UNNEST(JSON_QUERY_ARRAY(a.active_periods_json)) AS p
        WHERE (JSON_VALUE(p, '$.start') IS NULL OR CAST(JSON_VALUE(p, '$.start') AS INT64) <= @t)
          AND (JSON_VALUE(p, '$.end')   IS NULL OR CAST(JSON_VALUE(p, '$.end')   AS INT64) >  @t)
      )
```

(`JSON_VALUE` returns STRING, so the casts are required.) DuckDB:

```sql
FROM service_alerts a
WHERE a.active_periods_json IS NULL
   OR EXISTS (
        SELECT 1
        FROM unnest(json_transform(a.active_periods_json,
             '[{"start":"UBIGINT","end":"UBIGINT"}]')) AS u(p)
        WHERE (p.start IS NULL OR p.start <= $t)
          AND (p."end" IS NULL OR p."end" > $t)
      )
```

Producer-supplied text columns (`header_text`, `description_text`, `tts_*`,
`cause_detail`, `effect_detail`, `image_url`, headsigns, etc.) are unvalidated
third-party content passed through verbatim — the pipeline never interprets or
fetches them, but downstream renderers must treat them as untrusted input.

### Schedule

Assets are materialized daily at 2am UTC via `daily_compaction_schedule`, processing the previous day's data.

### Commands

```bash
# Start Dagster UI for development
uv run dg dev

# List all definitions
uv run dg list defs

# Validate definitions load correctly
uv run dg check defs

# Manually materialize an asset for a specific date and feed
uv run dg launch --assets vehicle_positions_parquet --partition "2026-01-01|gtfs.example.com/feed"
```

Partition keys are `date|feed`, where `feed` is the scheme-stripped feed URL (`~` prefix for `http`); the feed dimension is dynamic, so the key must already be registered.

---

## Project Structure

```
gtfs-realtime-archiver/
├── .github/
│   └── workflows/                  # CI/CD (lint, test, build, push, pages)
├── .dagster_home/                  # Local Dagster configuration
├── .tool-versions                  # asdf version pinning
├── tf/
│   ├── main.tf                     # Cloud Run service (archiver)
│   ├── storage.tf                  # GCS buckets (protobuf + parquet)
│   ├── iam.tf                      # Archiver service account
│   ├── dagster.tf                  # Dagster module instantiation (registry module)
│   ├── dagster_iam.tf              # Project-specific Dagster IAM grants
│   ├── artifact_registry.tf        # GHCR remote repository proxy
│   ├── bigquery.tf                 # BigQuery datasets and external tables
│   ├── dns.tf                      # DNS records for gtfsrt.io services
│   ├── tags.tf                     # Secret tags for feed API key access
│   ├── wif.tf                      # Workload Identity Federation (GitHub Actions)
│   ├── variables.tf                # Input variables
│   ├── outputs.tf                  # Output values
│   └── versions.tf                 # Provider versions
├── deploy/                         # Dagster deployment configs (baked into images)
│   ├── dagster.yaml
│   └── workspace.yaml
├── site/                           # Static site for gtfsrt.io (GitHub Pages)
├── src/
│   ├── gtfs_rt_archiver/           # Archiver service
│   │   ├── __init__.py
│   │   ├── __main__.py             # Entry point
│   │   ├── config.py               # Settings, feed loading and flattening
│   │   ├── models.py               # Pydantic models
│   │   ├── scheduler.py            # APScheduler setup (+ sharding)
│   │   ├── fetcher.py              # HTTP fetch logic
│   │   ├── storage.py              # GCS upload
│   │   ├── secrets.py              # Secret Manager integration
│   │   ├── metrics.py              # Prometheus metrics
│   │   ├── logging.py              # Structlog configuration
│   │   └── health.py               # Health/metrics HTTP server
│   └── dagster_pipeline/           # Data processing pipeline
│       ├── __init__.py
│       ├── definitions.py          # Dagster definitions entry point
│       └── defs/
│           ├── __init__.py
│           ├── partitions.py       # Partition definitions
│           ├── schedules.py        # Compaction schedules
│           ├── sensors.py          # Sensors
│           ├── resources/          # GCS and Secret Manager resources
│           └── assets/
│               ├── __init__.py
│               ├── compaction.py   # Protobuf → Parquet compaction assets
│               ├── schemas.py      # PyArrow schemas for feed types
│               ├── feeds_metadata.py  # Agency/feed config → Parquet
│               ├── inventory.py    # Bucket inventory for gtfsrt.io site
│               └── schedule.py     # GTFS Schedule ingestion assets
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Pytest fixtures (archiver)
│   ├── test_config.py
│   ├── test_fetcher.py
│   ├── test_health.py
│   ├── test_main.py
│   ├── test_models.py
│   ├── test_scheduler.py
│   ├── test_secrets.py
│   ├── test_storage.py
│   └── dagster/                    # Dagster pipeline tests
│       ├── __init__.py
│       ├── conftest.py             # Dagster test fixtures
│       ├── test_compaction.py      # Tests for extraction functions
│       └── test_partitions.py      # Partition helper tests
├── agencies.example.yaml           # Example agency configuration
├── .env.example                    # Environment variables template
├── Dockerfile                      # Archiver container build
├── Containerfile.dagster           # Dagster images (webserver, daemon, code-server)
├── docker-compose.yml              # Local dev stack
├── pyproject.toml                  # Project metadata (uv-managed)
├── uv.lock                         # Dependency lockfile
├── DESIGN.md                       # This document
└── README.md                       # Usage documentation
```

### Dependency Groups

The project uses component-specific dependency groups in `pyproject.toml`:

| Group | Purpose |
| ------- | --------- |
| `archiver` | Runtime deps for gtfs_rt_archiver |
| `dev-archiver` | Test deps for gtfs_rt_archiver |
| `dagster` | Runtime deps for dagster_pipeline |
| `dev-dagster` | Dev tools for dagster_pipeline |
| `dagster-deploy` | Deps for Dagster Cloud Run deployment |
| `dev` | Aggregate group (archiver, dev-archiver, dagster, dev-dagster + mypy, ruff) |

Install specific groups with `uv sync --only-group <name>` or all dev deps with `uv sync`.

---

## Implementation Plan

### Phase 1: Project Setup

1. **Initialize repository structure**
   - Create directory layout
   - Configure asdf with `.tool-versions`
   - Initialize Python project with `uv init`
   - Add core dependencies with `uv add`

2. **Set up development environment**
   - Configure asdf for python, uv, opentofu
   - Create Dockerfile with multi-stage build
   - Set up GitHub Actions for CI/CD

3. **Implement configuration loading**
   - Define Pydantic models for feeds and config
   - Implement YAML loading with env var substitution
   - Add validation and error handling

### Phase 2: Core Functionality

1. **Implement HTTP fetcher**
   - Create async httpx client with connection pooling
   - Add retry logic with tenacity
   - Implement timeout and error handling

2. **Implement storage writer**
   - Create async GCS client wrapper
   - Implement Hive-partitioned path generation
   - Add metadata file writing

3. **Implement scheduler**
   - Configure APScheduler with async support
   - Create per-feed job scheduling
   - Add misfire handling and overlap prevention

### Phase 3: Observability

1. **Add Prometheus metrics**
   - Define counter, histogram, and gauge metrics
   - Instrument fetch and upload operations
   - Create metrics HTTP endpoint

2. **Add structured logging**
   - Configure structlog with JSON output
   - Add context to all log messages
   - Implement log level configuration

3. **Implement health endpoint**
   - Create HTTP health check server
   - Report scheduler and feed status
   - Add startup and liveness probe support

### Phase 4: Infrastructure

1. **Create OpenTofu configuration**
    - Define Cloud Run service
    - Create GCS bucket for archives
    - Configure IAM and service accounts
    - Set up Secret Manager for API keys

2. **Finalize GitHub Actions**
    - Build container on every push
    - Tag with version on v* tags
    - Push to GitHub Container Registry

### Phase 5: Testing and Documentation

1. **Write tests**
    - Unit tests for config, fetcher, storage
    - Integration tests with mock servers
    - End-to-end test with real GCS (optional)

2. **Write documentation**
    - README with quickstart guide
    - Configuration reference
    - Deployment instructions

---

## Commands Reference

### Development Setup

```bash
# Install tools via asdf
asdf plugin add python
asdf plugin add uv
asdf plugin add opentofu

# Pin and install versions
asdf set python 3.12
asdf set uv latest
asdf set opentofu latest
asdf install

# Initialize project (first time only)
uv init --name gtfs-rt-archiver --package

# Add dependencies
uv add httpx
uv add "apscheduler>=4.0.0a1"
uv add pydantic pydantic-settings
uv add gcloud-aio-storage
uv add prometheus-client
uv add structlog
uv add tenacity
uv add pyyaml

# Add dev dependencies
uv add --dev pytest pytest-asyncio pytest-httpx
uv add --dev ruff mypy
uv add --dev respx  # For mocking httpx

# Run locally
uv run python -m gtfs_rt_archiver

# Run tests
uv run pytest

# Type check
uv run mypy src/

# Lint
uv run ruff check src/
uv run ruff format src/
```

### Container Build

```bash
# Build locally
docker build -t gtfs-rt-archiver .

# Run locally with GCS credentials
docker run \
  -v ~/.config/gcloud:/root/.config/gcloud:ro \
  -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
  -e GCS_BUCKET_RT_PROTOBUF=my-test-bucket \
  -p 8080:8080 \
  gtfs-rt-archiver
```

### Infrastructure Deployment

```bash
cd tf/

# Initialize OpenTofu
tofu init

# Plan changes
tofu plan -var-file=prod.tfvars

# Apply changes
tofu apply -var-file=prod.tfvars

# Destroy (careful!)
tofu destroy -var-file=prod.tfvars
```

The Dagster deployment supports two topologies via `dagster_deployment_mode`:
`split` (default; each component its own Cloud Run resource) and `consolidated`
(webserver + daemon + code server as three containers in one always-on instance —
lowest cost floor, single code location only). Constraints and cost break-even are
documented in "Deployment Topologies" in `.claude/CLAUDE.md`.

Note that releases deploy by running `tofu apply` with image `-var`s derived from
the release tag (see `.github/workflows/deploy.yaml`) — local applies must supply
current image versions or they will roll deployed images back.

The Dagster deployment module is consumed from the Terraform Registry
(`JarvusInnovations/dagster-cloud-run/google`, extracted from this repo): this
project's buckets, secrets, and env are wired through the module's `extra_env`,
`bucket_grants`, `secret_grants`, and `run_worker_secret_env` maps in
`tf/dagster.tf`. Topology modes, ingress postures, and the deployment kit are
documented in the module repo
(JarvusInnovations/terraform-google-dagster-cloud-run).

---

## Appendix A: Dependency Justification

| Dependency | Purpose | Alternatives Considered |
| ------------ | --------- | ------------------------ |
| **httpx** | Async HTTP client | aiohttp (less ergonomic), requests (sync only) |
| **apscheduler** | In-process job scheduling | schedule (no async), celery (overkill) |
| **pydantic** | Data validation & settings | attrs (less features), dataclasses (no validation) |
| **gcloud-aio-storage** | Async GCS client | google-cloud-storage (sync), aiogoogle (less mature) |
| **prometheus-client** | Metrics export | opentelemetry (more complex), statsd (different model) |
| **structlog** | Structured logging | python-json-logger (less features), loguru (different API) |
| **tenacity** | Retry logic | backoff (less features), stamina (newer, less proven) |

## Appendix B: Migration from Existing Systems

### From data-infra (Cal-ITP)

1. Export Airtable feed configs to YAML format
2. Map `GTFSDownloadConfig` fields to new `FeedConfig` model
3. Update GCS paths to new partition scheme (or keep compatible)
4. Deploy new archiver alongside existing for validation
5. Compare output files for parity
6. Cut over when confident

### From transit-data-analytics-demo

1. Convert `agencies.yaml` to new format (mostly compatible)
2. Update `feed_type` enum values if needed
3. Remove Redis dependency from Kubernetes manifests
4. Deploy and validate

## Appendix C: Future Enhancements

- **Feed discovery**: Periodic scan of GTFS-RT registry for new feeds
- **Content validation**: Optional protobuf parsing and validation
- **Deduplication**: ETag/Last-Modified checking to skip unchanged content
- **Compression**: gzip compression for storage cost reduction
- **Notifications**: Slack/email alerts for persistent feed failures
- **Dashboard**: Grafana dashboard for operational visibility
