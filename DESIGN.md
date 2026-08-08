# GTFS-RT Archiver Design Document

> **This document is not normative.** The source of truth for what *should be
> true* of this system is [`specs/`](specs/README.md) — architecture in
> [`specs/architecture.md`](specs/architecture.md), behavior in
> [`specs/behaviors/`](specs/behaviors/), and governing principles in
> [`specs/principles.md`](specs/principles.md). This document holds the
> background, rationale, and history behind those decisions: the *why*, not
> the *what*. When the two disagree, the specs win.

## Overview

A lightweight, resilient service for archiving GTFS-Realtime feeds to cloud
storage at configurable intervals, paired with a Dagster pipeline that compacts
the raw archives into analytics-ready parquet. Designed to handle hundreds of
feeds cheaply and reliably on Google Cloud Run.

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
- Parse or validate GTFS-RT content in the archiver (raw archival only)
- Support non-HTTP feed sources

The first three hardened into standing principles — see
[`specs/principles.md`](specs/principles.md) ("Missed data has no value late",
"Archive verbatim, transform later").

## Design decisions and rationale

### Archiver: single container, no broker

The problem statement above is the rationale: GTFS-RT archiving needs neither
durability nor work distribution, so a queue/broker buys only failure modes.
In-memory APScheduler with misfire-drop and latest-only coalescing matches the
domain — a missed snapshot is worthless a minute later. Horizontal growth is
handled by deterministic hash sharding (`SHARD_INDEX`/`TOTAL_SHARDS`) rather
than shared state.

### Storage tiering asymmetry

The protobuf bucket deliberately has **no** storage-class tiering: it holds
millions of tiny objects, and lifecycle transitions bill a Class A operation
per object at the destination class's rate — measured at ~9× the storage cost
itself (June 2026: ~$305/mo in transition ops vs ~$34/mo storage). Objects stay
STANDARD until the free age-365 delete reaps them. The parquet bucket has few,
large objects, so Nearline/Coldline tiering pays for itself there. Current
lifecycle rules: [`specs/behaviors/published-artifacts.md`](specs/behaviors/published-artifacts.md#retention-and-access-tfstoragetf).

### Compaction design decisions

| Decision | Rationale |
| ---------- | ----------- |
| **Daily partitions** | Medium feed count (~100s) makes daily batches practical without excessive memory usage |
| **Sensor-driven feed discovery** | The raw bucket is the source of feed existence — no static registry to maintain; the per-type 2am schedules backstop completeness for known feeds |
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
with repeated structures JSON-encoded instead. The normative statement of
both grains lives in
[`specs/behaviors/compaction.md`](specs/behaviors/compaction.md#tables-and-grain).

### Historical semantic migrations

Two column conventions migrated at v0.9.3, leaving deliberate per-partition
boundaries in published data (accepted on PR #94 in preference to carrying a
permanent cross-table asymmetry): `license_plate` moved to per-field presence
(older vehicle_positions partitions contain `""` where newer ones read NULL),
and `feed_timestamp` moved to per-field presence in the other value domain (an
explicit `header.timestamp = 0` now records `0` where it previously recorded
NULL). The normative semantics, exhaustive enum-presence lists, and
boundary-spanning query guidance live in
[`specs/behaviors/compaction.md`](specs/behaviors/compaction.md#presence-semantics).

## Where the normative content went

| Formerly in this document | Now specified in |
| --- | --- |
| Component architecture, data flow, orchestration | [`specs/architecture.md`](specs/architecture.md) |
| Storage path structure, metadata sidecar format, retention, inventory/feeds.parquet shapes | [`specs/behaviors/published-artifacts.md`](specs/behaviors/published-artifacts.md) |
| Fetch scheduling, retry taxonomy, failure categories, sharding, health/metrics surface | [`specs/behaviors/archiving.md`](specs/behaviors/archiving.md) |
| Parquet table grains, column families, presence/JSON/translation semantics, complete-capture policy, failure and overwrite rules | [`specs/behaviors/compaction.md`](specs/behaviors/compaction.md) |
| Feed configuration schema (`agencies.yaml`) | [README.md](README.md) (usage) + `src/gtfs_rt_archiver/models.py` (validation) |
| Environment variables, commands, deployment reference | [README.md](README.md) and [.claude/CLAUDE.md](.claude/CLAUDE.md) |

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
