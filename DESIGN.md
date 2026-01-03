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
|------|-------------|
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
│                     GTFS-RT Archiver Container                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                      Python Async Runtime                       │ │
│  │                                                                 │ │
│  │  ┌─────────────────┐     ┌───────────────────────────────────┐ │ │
│  │  │   APScheduler   │     │         Fetch Worker Pool         │ │ │
│  │  │                 │     │                                   │ │ │
│  │  │ • Per-feed jobs │────▶│ • httpx.AsyncClient               │ │ │
│  │  │ • Cron triggers │     │ • Semaphore(max_concurrent)       │ │ │
│  │  │ • Misfire grace │     │ • Retry with exponential backoff  │ │ │
│  │  └─────────────────┘     └───────────────────────────────────┘ │ │
│  │                                         │                       │ │
│  │                                         ▼                       │ │
│  │                          ┌───────────────────────────────────┐ │ │
│  │                          │       Storage Writer              │ │ │
│  │                          │                                   │ │ │
│  │                          │ • gcloud-aio-storage (async)      │ │ │
│  │                          │ • Hive-partitioned paths          │ │ │
│  │                          │ • Configurable bucket/prefix      │ │ │
│  │                          └───────────────────────────────────┘ │ │
│  │                                                                 │ │
│  │  ┌─────────────────┐     ┌───────────────────────────────────┐ │ │
│  │  │  Health Server  │     │       Metrics Server              │ │ │
│  │  │  (port 8080)    │     │       (port 9090)                 │ │ │
│  │  └─────────────────┘     └───────────────────────────────────┘ │ │
│  │                                                                 │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                         ┌────────────────────┐
                         │   Google Cloud     │
                         │   Storage (GCS)    │
                         └────────────────────┘
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

#### Health Server

- HTTP endpoint at `/health` for liveness probes
- Returns scheduler state and active job count
- Used by Cloud Run and Kubernetes for health checks

#### Metrics Server

- Prometheus metrics endpoint at `/metrics`
- Exposes fetch duration, success/error counts, active feeds
- Per-feed labels for granular observability

---

## Data Model

### Feed Configuration

```yaml
# feeds.yaml
defaults:
  interval_seconds: 20
  timeout_seconds: 30
  retry:
    max_attempts: 3
    backoff_base: 1.0
    backoff_max: 10.0

feeds:
  - id: septa-vehicle-positions
    name: SEPTA Vehicle Positions
    url: https://www3.septa.org/gtfsrt/septa-pa-us/Vehicle/rtVehiclePosition.pb
    feed_type: vehicle_positions
    agency: septa
    # Uses defaults, no auth required

  - id: mta-vehicles
    name: MTA Vehicles
    url: https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs
    feed_type: vehicle_positions
    agency: mta
    auth:
      type: header                # Auth via HTTP header
      secret_name: mta-api-key    # Secret name in GCP Secret Manager
      key: x-api-key              # Header name
      value: "${SECRET}"          # ${SECRET} replaced with secret value

  - id: bart-trip-updates
    name: BART Trip Updates
    url: https://api.bart.gov/gtfsrt/tripupdate.aspx
    feed_type: trip_updates
    agency: bart
    interval_seconds: 15
    auth:
      type: query                 # Auth via query parameter
      secret_name: bart-api-key
      key: key
      value: "${SECRET}"
```

### Pydantic Models

```python
from pydantic import BaseModel, HttpUrl, Field
from typing import Optional
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
    secret_name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    key: str
    value: str = "${SECRET}"
    resolved_value: str | None = Field(default=None, exclude=True)

class RetryConfig(BaseModel):
    max_attempts: int = 3
    backoff_base: float = 1.0
    backoff_max: float = 10.0

class FeedConfig(BaseModel):
    id: str = Field(..., pattern=r"^[a-z0-9-]+$")
    name: str
    url: HttpUrl
    feed_type: FeedType
    agency: Optional[str] = None
    interval_seconds: int = Field(default=20, ge=5, le=3600)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    auth: AuthConfig | None = None

class ArchiverConfig(BaseModel):
    bucket: str
    max_concurrent: int = Field(default=100, ge=1, le=500)
    defaults: FeedConfig  # Partial, used for defaults
    feeds: list[FeedConfig]
```

### Storage Path Structure

```
gs://{bucket}/
└── {feed_type}/
    └── date={YYYY-MM-DD}/
        └── hour={YYYY-MM-DDTHH:00:00Z}/
            └── base64url={encoded-url}/
                ├── {ISO8601_timestamp}.pb      # Raw protobuf
                └── {ISO8601_timestamp}.meta    # Optional metadata JSON
```

The `base64url` partition contains the URL-safe base64 encoding of the base feed URL (without auth query parameters), without padding characters. This ensures consistent storage paths across secret rotations and prevents secret leakage in storage paths.

Example:

```
gs://my-gtfs-archive/
└── vehicle_positions/
    └── date=2025-01-15/
        └── hour=2025-01-15T14:00:00Z/
            └── base64url=aHR0cHM6Ly93d3czLnNlcHRhLm9yZy9ndGZzcnQvc2VwdGEtcGEtdXMvVmVoaWNsZS9ydFZlaGljbGVQb3NpdGlvbi5wYg/
                ├── 2025-01-15T14:20:00.000Z.pb
                ├── 2025-01-15T14:20:00.000Z.meta
                ├── 2025-01-15T14:20:20.000Z.pb
                └── 2025-01-15T14:20:20.000Z.meta
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
|----------|-------------|---------|
| `CONFIG_PATH` | Path to feeds.yaml | `./feeds.yaml` |
| `GCS_BUCKET` | Target GCS bucket | Required |
| `GCP_PROJECT_ID` | GCP project ID for Secret Manager | Required if auth used |
| `MAX_CONCURRENT` | Max concurrent fetches | `100` |
| `HEALTH_PORT` | Health check server port | `8080` |
| `METRICS_PORT` | Prometheus metrics port | `9090` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FORMAT` | `json` or `text` | `json` |

### Secret Manager Integration

Feed authentication secrets are fetched from GCP Secret Manager at startup:

```yaml
auth:
  type: header                # header or query
  secret_name: mta-api-key    # Secret name in GCP Secret Manager
  key: x-api-key              # Header name or query param name
  value: "${SECRET}"          # ${SECRET} replaced with secret value
```

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
|----------|----------|---------|
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
  "status": "healthy",
  "scheduler": {
    "running": true,
    "jobs_scheduled": 45,
    "jobs_pending": 2
  },
  "feeds": {
    "total": 45,
    "active": 45,
    "erroring": 2
  },
  "uptime_seconds": 3600
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
        name  = "GCS_BUCKET"
        value = google_storage_bucket.archive.name
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
|------------|-----------|---------------|
| 1-100 | 1 | Single instance, 100 concurrent |
| 100-300 | 1-2 | Increase max_concurrent or add instance |
| 300-500 | 2-3 | Shard feeds across instances |
| 500+ | 3+ | Hash-based sharding |

#### Sharding Implementation

```python
# When SHARD_INDEX and TOTAL_SHARDS are set
shard_index = int(os.environ.get("SHARD_INDEX", 0))
total_shards = int(os.environ.get("TOTAL_SHARDS", 1))

def should_handle_feed(feed: FeedConfig) -> bool:
    return hash(feed.id) % total_shards == shard_index

active_feeds = [f for f in all_feeds if should_handle_feed(f)]
```

---

## Project Structure

```
gtfs-realtime-archiver/
├── .github/
│   └── workflows/
│       └── build.yaml              # Container build + push
├── .tool-versions                  # asdf version pinning
├── tf/
│   ├── main.tf                     # Cloud Run service
│   ├── storage.tf                  # GCS bucket
│   ├── iam.tf                      # Service account
│   ├── secrets.tf                  # Secret Manager
│   ├── variables.tf                # Input variables
│   ├── outputs.tf                  # Output values
│   └── versions.tf                 # Provider versions
├── src/
│   └── archiver/
│       ├── __init__.py
│       ├── __main__.py             # Entry point
│       ├── config.py               # Settings and feed loading
│       ├── models.py               # Pydantic models
│       ├── scheduler.py            # APScheduler setup
│       ├── fetcher.py              # HTTP fetch logic
│       ├── storage.py              # GCS upload
│       ├── metrics.py              # Prometheus metrics
│       └── health.py               # Health check server
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Pytest fixtures
│   ├── test_config.py
│   ├── test_fetcher.py
│   ├── test_storage.py
│   └── test_integration.py
├── feeds.yaml                      # Feed configuration
├── Dockerfile
├── pyproject.toml                  # Project metadata (uv-managed)
├── uv.lock                         # Dependency lockfile
├── DESIGN.md                       # This document
└── README.md                       # Usage documentation
```

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
uv run python -m archiver

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
  -e GCS_BUCKET=my-test-bucket \
  -p 8080:8080 \
  -p 9090:9090 \
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

---

## Appendix A: Dependency Justification

| Dependency | Purpose | Alternatives Considered |
|------------|---------|------------------------|
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

1. Convert `feeds.yaml` to new format (mostly compatible)
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
