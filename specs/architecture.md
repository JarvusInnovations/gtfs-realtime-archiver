# Architecture

What the system is composed of and how data flows through it. Rationale and
history live in [DESIGN.md](../DESIGN.md); this file declares what must be true.

## Components

| Component | Runs as | Responsibility |
| --- | --- | --- |
| **Archiver service** (`src/gtfs_rt_archiver/`) | Single always-on Cloud Run service (1 instance, CPU always allocated) | Fetch every configured GTFS-RT feed on its interval and write the raw bytes to the protobuf bucket. See [behaviors/archiving.md](behaviors/archiving.md). |
| **Dagster pipeline** (`src/dagster_pipeline/`) | Dagster on Cloud Run (webserver, daemon, code server, per-run worker Jobs) | Compact raw protobuf into daily parquet, maintain feed metadata, publish inventories, ingest GTFS Schedule feeds. See [behaviors/compaction.md](behaviors/compaction.md). |
| **Protobuf bucket** (`gs://protobuf.gtfsrt.io`) | GCS, private | Raw snapshots + `.meta` sidecars, Hive-partitioned; deleted at 365 days, no storage-class tiering. |
| **Parquet bucket** (`gs://parquet.gtfsrt.io`) | GCS, **public read**, CORS-open for GET/HEAD | All published artifacts (parquet tables, inventories, schedule extracts); kept indefinitely (Nearline @90d, Coldline @180d). See [behaviors/published-artifacts.md](behaviors/published-artifacts.md). |
| **BigQuery external tables** (`tf/bigquery.tf`) | BigQuery over the parquet bucket | SQL access to the parquet tables without ingestion. |
| **gtfsrt.io site** (`site/`) | GitHub Pages | Static single-page site rendering `inventory.json` on each load. |

All infrastructure is declared in `tf/` (OpenTofu); releases deploy images by
running `tofu apply` with image variables derived from the release tag.

## Data flow

```
feeds ──(archiver, per-feed interval)──▶ protobuf bucket ──(Dagster daily compaction)──▶ parquet bucket ──▶ consumers
                                                                                             ▲
agencies.yaml (Secret Manager) ──▶ feeds_metadata ──▶ feeds.parquet ──▶ bucket_inventory ────┘ (inventory.json, schedules.json)
```

## Configuration

- Feed configuration is a nested `agencies.yaml` (agencies → optional systems →
  feeds) stored in Secret Manager; the archiver and the metadata assets both
  flatten it through the same `flatten_agencies()` path so feed identity,
  inheritance (auth, intervals, timeouts, schedule URLs), and generated feed
  IDs (`{agency}[-{system}]-{feed-type}`) agree everywhere.
- Feed auth secrets live in GCP Secret Manager, tagged `type=feed-key`; IAM
  conditions restrict the archiver service account to tagged secrets.

## Orchestration model (Dagster)

- **Partitioning**: compaction assets are partitioned two-dimensionally as
  `date|feed`. Dates are UTC days (`end_offset=1` — today is never available).
  Feeds are **per-type dynamic partitions** (`vehicle_positions_feeds`,
  `trip_updates_feeds`, `service_alerts_feeds`) keyed by scheme-stripped feed
  URL (`https://` dropped; `~` prefix marks `http://`).
- **Feed discovery**: the `feed_discovery_sensor` (5-minute interval) scans
  yesterday's raw prefixes per feed type, registers unseen feeds as dynamic
  partitions, and requests runs for discovered feed+date combinations. There
  is no static feed registry in the pipeline; the raw bucket is the source of
  feed existence.
- **Daily compaction**: three per-type schedules (`vehicle_positions_schedule`,
  `trip_updates_schedule`, `service_alerts_schedule`) fire at 02:00 UTC and
  request one run per *known* (already-registered) feed partition for
  yesterday's date — the schedule is the completeness backstop, the sensor is
  the novelty path.
- **Inventory**: `inventory_job` (feeds_metadata → bucket_inventory) runs daily
  at 04:00 UTC, rebuilding `feeds.parquet`, `inventory.json`, and
  `schedules.json` from a full bucket scan.
- **GTFS Schedule**: `gtfs_schedule_check` runs daily at 05:00 UTC; a sensor
  triggers `gtfs_schedule_ingest` runs (partitioned by schedule URL, same
  stripped-URL key format) only for URLs whose feed fingerprint is new.

## Foundational decisions

- Python ≥3.12, `uv`-managed, mypy strict, async throughout the archiver
  (httpx, gcloud-aio-storage, aiohttp, APScheduler).
- Single-container archiver, in-memory scheduling, no broker or queue — see
  [principles.md](principles.md#missed-data-has-no-value-late).
- Pydantic models validate all configuration at load time.
- Structured logging (structlog; JSON in production), Prometheus metrics, and
  health endpoints on one port per service.
- Two Dagster deployment topologies (`split` / `consolidated`) are supported;
  constraints and cost trade-offs are documented in `.claude/CLAUDE.md` and the
  Terraform module repo (`JarvusInnovations/terraform-google-dagster-cloud-run`).
