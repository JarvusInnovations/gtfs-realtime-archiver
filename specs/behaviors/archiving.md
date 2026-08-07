# Behavior: Raw feed archiving

## Rule

Every configured GTFS-RT feed is fetched on its configured interval and the
response bytes are written verbatim to the protobuf bucket, with a JSON `.meta`
sidecar recording fetch provenance. Nothing is parsed, validated, or mutated on
the way through.

## Applies to

The archiver service (`src/gtfs_rt_archiver/`): scheduler, fetcher, storage
writer, health/metrics server.

## Scheduling

- One in-memory job per feed at `interval_seconds` (feed override → per-type
  default from `defaults.intervals`). No job store persistence.
- Feed start times are staggered by a deterministic offset
  (`md5(feed.id) % interval_seconds`) so feeds don't all fire simultaneously
  at startup, and the offset survives restarts. (Hash-based spread is
  approximate; rank-based even spacing is tracked as #60.)
- A tick that can't run within its misfire grace (default 5s) is dropped, and
  missed ticks coalesce to the latest — never queued or backfilled
  ([principle](../principles.md#missed-data-has-no-value-late)).
- At most one concurrent run per feed; feeds fail independently of each other
  (no circuit breaker).
- Horizontal sharding: when `SHARD_INDEX`/`TOTAL_SHARDS` are set, a feed is
  handled iff `md5(feed.id) % total_shards == shard_index` — deterministic
  across processes, every feed owned by exactly one shard.

## Fetching

- Async HTTP with a global concurrency semaphore (`MAX_CONCURRENT`, default
  100) and per-feed timeout (default 30s).
- Auth (header or query, from Secret Manager) is applied per feed config;
  resolved secret values never appear in logs, paths, or stored artifacts
  ([principle](../principles.md#secrets-never-reach-storage-paths-or-published-artifacts)).
- Retry with exponential backoff (per-feed `max_attempts`/`backoff_base`/
  `backoff_max`) applies **only** to transient failures: transport errors,
  timeouts, and 5xx responses. Client-error statuses 400/401/403/404/410 are
  never retried — logged and skipped until the next tick.

## Storage writes

- Object path: `{feed_type}/date={YYYY-MM-DD}/hour={YYYY-MM-DDTHH:00:00Z}/base64url={encoded}/{ISO8601-ms}Z.pb`,
  all partitions derived from the **fetch timestamp** (UTC). Layout and
  encoding rules are contract — see
  [published-artifacts.md](published-artifacts.md#raw-protobuf-layout).
- Content is uploaded as `application/x-protobuf`, byte-for-byte as received.
- Unless sidecars are disabled, a `.meta` JSON object with the same path stem
  records feed identity (feed_id, agency, system, schedule_url, url), fetch
  timing (`fetch_timestamp`, `duration_ms`), and response metadata
  (`response_code`, `content_length`, `content_type`, and only the
  etag / last-modified / content-type / content-length headers).
- Compaction depends on `fetch_timestamp` in the sidecar; a missing or invalid
  sidecar degrades that column to NULL downstream, it does not block capture.

## Observability surface

The service exposes, on one port (`HEALTH_PORT`, default 8080):

- `GET /health` — liveness: version, uptime, scheduler running state, job and
  feed counts
- `GET /health/feeds` — per-feed status detail
- `GET /ready` — readiness
- `GET /metrics` — Prometheus: fetch/upload counters (success/error, per feed,
  type, and agency), duration and size histograms, active-feed and job gauges,
  last-fetch timestamps

Structured logs (structlog; JSON in production) record every fetch success and
failure with feed identity, timing, and error context.

## Principles

**Inherited** — from [principles.md](../principles.md):

- [Archive verbatim, transform later](../principles.md#archive-verbatim-transform-later)
  — the archiver must never parse or judge feed content; a feed serving garbage
  bytes is archived as faithfully as a healthy one.
- [Missed data has no value late](../principles.md#missed-data-has-no-value-late)
  — drop, coalesce, and move on; no queue, no retry-after-the-fact.
- [Secrets never reach storage paths or published artifacts](../principles.md#secrets-never-reach-storage-paths-or-published-artifacts).
