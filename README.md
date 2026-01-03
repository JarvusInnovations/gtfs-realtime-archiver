# GTFS-RT Archiver

A lightweight, resilient service for archiving GTFS-Realtime feeds to Google Cloud Storage at configurable intervals.

## Overview

GTFS-RT Archiver is a single-container Python service designed to:

- Fetch hundreds of GTFS-Realtime feeds reliably and efficiently
- Archive raw protobuf responses to GCS with Hive-style partitioning
- Handle network failures, feed outages, and transient errors gracefully
- Provide comprehensive observability via Prometheus metrics and structured logging

**Key Features:**

- Async I/O for high concurrency (500+ feeds with <1GB memory)
- Per-feed configurable intervals (5-3600 seconds)
- Exponential backoff retry for transient failures
- Deterministic sharding for horizontal scaling
- Cloud Run and Kubernetes ready

## Architecture

```
┌─────────────────────────────────────────┐
│    GTFS-RT Archiver Container           │
├─────────────────────────────────────────┤
│  APScheduler (per-feed jobs)            │
│        ↓                                │
│  httpx.AsyncClient (fetch feeds)        │
│        ↓                                │
│  gcloud-aio-storage (upload to GCS)     │
│        ↓                                │
│  Prometheus metrics + Health endpoints  │
└─────────────────────────────────────────┘
           ↓
    Google Cloud Storage
    (Hive-partitioned archives)
```

See [DESIGN.md](DESIGN.md) for detailed architecture and implementation notes.

### Storage Layout

```
gs://{bucket}/
└── {feed_type}/
    └── date={YYYY-MM-DD}/
        └── hour={YYYY-MM-DDTHH:00:00Z}/
            └── base64url={encoded-url}/
                ├── {timestamp}.pb    # Raw protobuf
                └── {timestamp}.meta  # Metadata JSON
```

This structure enables efficient queries in BigQuery or Athena by partitioning on feed type, date, and hour. The `base64url` partition uniquely identifies each feed by its base URL.

## Developer Quickstart

### Prerequisites

- [asdf](https://asdf-vm.com/) for version management
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) for dependency management

### Setup

```bash
# Install tools via asdf
asdf plugin add python
asdf plugin add uv
asdf install

# Install dependencies
uv sync --dev

# Copy example configuration
cp agencies.example.yaml agencies.yaml
# Edit agencies.yaml with your agency/feed URLs

# Set required environment variables
export GCS_BUCKET=my-test-bucket
```

### Development Commands

```bash
# Run locally
uv run python -m gtfs_rt_archiver

# Run tests
uv run pytest tests/ -v

# Type check
uv run mypy src/

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Build container
docker build -t gtfs-rt-archiver .

# Run container locally (requires GCS credentials)
docker run \
  -v ~/.config/gcloud:/root/.config/gcloud:ro \
  -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
  -e GCS_BUCKET=my-bucket \
  -p 8080:8080 \
  gtfs-rt-archiver
```

### Local Development with Docker Compose

For local development without GCS credentials, use Docker Compose with a fake GCS server:

```bash
# Copy example configuration
cp agencies.example.yaml agencies.yaml
cp .env.example .env

# Start services (fake-gcs + archiver)
docker compose up --build

# In another terminal, verify feeds are being archived
ls -la data/test-bucket/
curl -s http://localhost:8080/health | jq
curl -s http://localhost:4443/storage/v1/b/test-bucket/o | jq '.items | length'
```

The `data/` directory contains archived feeds in Hive-partitioned structure:

```
data/test-bucket/
├── service_alerts/agency=septa/dt=2026-01-02/hour=03/...
├── trip_updates/agency=septa/dt=2026-01-02/hour=03/...
└── vehicle_positions/agency=septa/dt=2026-01-02/hour=03/...
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCS_BUCKET` | Yes | - | Target GCS bucket name |
| `GCP_PROJECT_ID` | If auth used | - | GCP project ID for Secret Manager |
| `CONFIG_PATH` | No | `./agencies.yaml` | Path to agencies configuration file |
| `MAX_CONCURRENT` | No | `100` | Maximum concurrent fetches |
| `HEALTH_PORT` | No | `8080` | Port for health/metrics server |
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FORMAT` | No | `json` | Log format (json or text) |
| `SHARD_INDEX` | No | `0` | Shard index for multi-instance deployments |
| `TOTAL_SHARDS` | No | `1` | Total number of shards |

### Agency Configuration (`agencies.yaml`)

```yaml
defaults:
  interval_seconds: 20      # Fetch every 20 seconds
  timeout_seconds: 30       # HTTP timeout
  retry:
    max_attempts: 3
    backoff_base: 1.0
    backoff_max: 10.0

feeds:
  - id: septa-vehicles
    name: SEPTA Vehicle Positions
    url: https://www3.septa.org/gtfsrt/septa-pa-us/Vehicle/rtVehiclePosition.pb
    feed_type: vehicle_positions  # vehicle_positions | trip_updates | service_alerts
    agency: septa
    interval_seconds: 15          # Override default

  # Feed with header authentication (via GCP Secret Manager)
  - id: mta-vehicles
    name: MTA Vehicles
    url: https://api.mta.info/feeds
    feed_type: vehicle_positions
    agency: mta
    auth:
      type: header              # Auth via HTTP header
      secret_name: mta-api-key  # Secret name in GCP Secret Manager
      key: x-api-key            # Header name
      # value field is optional - when omitted, uses entire secret directly

  # Feed with query parameter authentication
  - id: bart-trips
    name: BART Trip Updates
    url: https://api.bart.gov/gtfsrt/tripupdate.aspx
    feed_type: trip_updates
    agency: bart
    auth:
      type: query               # Auth via query parameter
      secret_name: bart-api-key
      key: key                  # Query parameter name

  # Advanced: Feed with templated authentication (e.g., Bearer token)
  - id: github-feed
    name: GitHub GTFS Feed
    url: https://api.github.com/repos/example/gtfs
    feed_type: vehicle_positions
    agency: github
    auth:
      type: header
      secret_name: github-token
      key: Authorization
      value: "Bearer ${SECRET}"  # Template to prefix secret with "Bearer "
```

### Feed Authentication

Feeds can authenticate via HTTP headers or query parameters using secrets stored in GCP Secret Manager.

**Configuration:**

- `type`: `header` or `query` - authentication method
- `secret_name`: name of the secret in GCP Secret Manager
- `key`: header name or query parameter name
- `value`: (optional) template string with `${SECRET}` placeholder
  - When omitted: uses the entire secret value directly
  - When provided: replaces `${SECRET}` with the secret value (e.g., `"Bearer ${SECRET}"`)

**Creating secrets:**

```bash
# Create the secret
gcloud secrets create mta-api-key --replication-policy=automatic

# Add the secret value
echo -n "your-api-key" | gcloud secrets versions add mta-api-key --data-file=-

# Tag the secret for IAM access (if using Terraform-managed tags)
gcloud resource-manager tags bindings create \
  --tag-value=${PROJECT_ID}/type/feed-key \
  --parent=//secretmanager.googleapis.com/projects/${PROJECT_ID}/secrets/mta-api-key \
  --location=global
```

### API Endpoints

- **GET /health** - Health check with scheduler status
- **GET /ready** - Kubernetes readiness probe
- **GET /metrics** - Prometheus metrics

### Prometheus Metrics

Key metrics exposed on `/metrics`:

- `gtfs_rt_fetch_total` - Total fetch attempts by feed
- `gtfs_rt_fetch_success_total` - Successful fetches
- `gtfs_rt_fetch_errors_total{error_type}` - Failed fetches by error type
- `gtfs_rt_fetch_duration_seconds` - Fetch latency histogram
- `gtfs_rt_upload_errors_total{error_type}` - Upload failures by error type
- `gtfs_rt_active_feeds` - Number of feeds being processed

All metrics include `feed_id`, `feed_type`, and `agency` labels.

## License

AGPL-3.0
