# Behavior: Published artifacts (cross-repo data contracts)

## Rule

Everything in the parquet bucket (`gs://parquet.gtfsrt.io`, public read,
CORS-open for GET/HEAD) is a **published contract**: external repos and
products consume these paths and shapes programmatically. Every artifact below
must keep its path, shape, and semantics stable, evolving **additively only**
([principle](../principles.md#published-artifacts-evolve-additively)).

The raw protobuf bucket (`gs://protobuf.gtfsrt.io`) is private, but its layout
is a contract for this repo's own compaction and at least one external
consumer, so it is specified here too.

Known consumers are from an org-wide code search (2026-07-16, issue #86) — a
point-in-time floor, not a closed set. The bucket is public; unknown consumers
must be assumed to exist.

## Change process

Before changing anything this file specifies:

1. Update this spec first (spec-first flow), flagging the change as additive or
   breaking.
2. Search the known consumer repos (transit-lake, gtfsrt-sandbox, `site/` in
   this repo) for the surface being changed.
3. Breaking changes (rename/remove/repurpose a path, column, or key; semantic
   change to existing values) require coordinating every known consumer and a
   release-notes callout. Additive changes require neither, but new columns
   only populate from ship-date forward — consumers read schema-tolerantly
   (`union_by_name` in DuckDB; BigQuery external tables return NULL for absent
   columns).

## Artifacts

### RT parquet tables (Hive layout)

```
gs://parquet.gtfsrt.io/{feed_type}/date={YYYY-MM-DD}/base64url={encoded}/data.parquet
```

- `feed_type` ∈ `vehicle_positions`, `trip_updates`, `service_alerts`,
  `trip_modifications`, `shapes`, `stops`.
- One file per feed per UTC day, rewritten wholesale on re-materialization.
  A day/feed with zero records has **no file** (never an empty file).
- Column schemas and semantics: [compaction.md](compaction.md). Authoritative
  column registry: `src/dagster_pipeline/defs/assets/schemas.py`, held complete
  by the field-coverage manifest tests.
- Compression: zstd.
- **Consumers**: transit-lake dbt (`packages/dbt-gtfs-rt/macros/rt_helpers.sql`,
  tenant `sources.yml`); gtfsrt-sandbox (docs/scripts); BigQuery external
  tables (`tf/bigquery.tf`).

### `inventory.json` (bucket root)

Rebuilt daily at 04:00 UTC by the `bucket_inventory` asset. A JSON **array** of
per-feed objects aggregated across the three primary feed types
(`vehicle_positions`, `trip_updates`, `service_alerts` — the entity-grain
tables are deliberately excluded from the scan so their rows don't inflate feed
counts; surfacing them is future additive work):

```json
{
  "url": "https://…",            // feed URL (from feeds.parquet; feeds without a mapping are omitted)
  "base64url": "…",
  "agency_id": "…", "agency_name": "…",      // null when unknown
  "system_id": "…", "system_name": "…",      // null when feed has no system / unknown
  "feed_type": "…",
  "date_min": "YYYY-MM-DD", "date_max": "YYYY-MM-DD",
  "total_records": 0,
  "total_bytes": 0
}
```

- **Consumers**: gtfsrt.io (`site/app.js`, fetched on every page load);
  transit-lake (`apps/server/src/config.ts`, typed in
  `packages/shared/src/types.ts`); gtfsrt-sandbox
  (`scripts/download_data.py`, `models/staging/stg_available_feeds.sql`).
  The transit-lake type definition is the reference typing of this shape —
  additive evolution is what keeps it valid.

### `schedules.json` (bucket root)

Rebuilt alongside `inventory.json`. JSON array, one object per schedule URL,
sorted by `schedule_url`, versions sorted by `date_retrieved`:

```json
{
  "schedule_url": "https://…",
  "base64url": "…",
  "versions": [
    {"_feed_digest": "…", "date_retrieved": "…", "feed_start_date": "…", "feed_end_date": "…"}
  ]
}
```

- **Consumers**: none found in the 2026-07-16 code search — verify before
  assuming unused; the bucket is public.

### `feeds.parquet` (bucket root)

Rebuilt daily at 04:00 UTC by the `feeds_metadata` asset from `agencies.yaml`
(Secret Manager). One row per configured feed; columns: `base64url`, `url`,
`feed_type`, `feed_id`, `feed_name`, `agency_id`, `agency_name`, `system_id`,
`system_name`, `interval_seconds`, `schedule_url`, `schedule_urls` (JSON
array string). Nullable: `system_id`, `system_name`, `schedule_url`,
`schedule_urls`.

- **Consumers**: `bucket_inventory` (internal join for agency attribution);
  possibly external — verify before breaking.

### GTFS Schedule extracts

```
gs://parquet.gtfsrt.io/schedules/base64url={encoded}/_feed_digest={fingerprint}/
    metadata.json + exploded per-file parquet (written by gtfs-digester)
```

- One immutable version directory per distinct feed fingerprint; ingestion is
  idempotent (an existing fingerprint is never rewritten).
- `metadata.json` includes at least `schedule_url`, `_feed_digest`,
  `date_retrieved`, `feed_start_date`, `feed_end_date` — the fields
  `schedules.json` republishes.

### Raw protobuf layout

```
gs://protobuf.gtfsrt.io/{feed_type}/date={YYYY-MM-DD}/hour={YYYY-MM-DDTHH:00:00Z}/base64url={encoded}/
    {ISO8601-ms}Z.pb      # verbatim response bytes
    {ISO8601-ms}Z.meta    # JSON fetch-provenance sidecar (see behaviors/archiving.md)
```

All partition values derive from the fetch timestamp (UTC). Consumers: this
repo's compaction and dashboards; possibly gtfsrt-sandbox.

### base64url encoding (shared by both buckets)

URL-safe base64 of the **base feed URL only** — auth query parameters excluded
— with padding stripped. This keeps paths stable across secret rotations and
free of credentials
([principle](../principles.md#secrets-never-reach-storage-paths-or-published-artifacts)).
Dagster partition keys use a related but distinct encoding: the scheme-stripped
URL (`~` prefix for `http://`).

## Retention and access (tf/storage.tf)

| Bucket | Access | Lifecycle |
| --- | --- | --- |
| `protobuf.gtfsrt.io` | private | delete at 365 days; **no storage-class tiering** (per-object transition ops would cost ~9× the storage) |
| `parquet.gtfsrt.io` | public read (`allUsers`), CORS `*` for GET/HEAD | Nearline @90d, Coldline @180d, **never deleted** |

Raw retention is an implicit contract: any consumer's lookback expectation
beyond 365 days can only be served by parquet, and re-materialization can only
backfill parquet columns while raw is alive
([principle](../principles.md#derived-data-outlives-raw); details in
[compaction.md](compaction.md#re-materialization-and-overwrite-semantics)).

## Principles

**Inherited** — from [principles.md](../principles.md):

- [Published artifacts evolve additively](../principles.md#published-artifacts-evolve-additively)
  — the governing rule for every surface in this file.
- [Derived data outlives raw](../principles.md#derived-data-outlives-raw) —
  parquet is the permanent record; raw expiry must never reach through to it.
