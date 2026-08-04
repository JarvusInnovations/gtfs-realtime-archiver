---
status: planned
depends: [proto-parquet-reconciliation-dashboard]
specs: []
issues: [91]
---

# Plan: Extract newly-visible GTFS-RT fields (issue #91 step 2)

## Scope

Add extraction, schema, and BigQuery columns for every GTFS-RT field the #91
census found populated in the fleet but currently dropped by compaction —
shipped **in the same release (v0.9.3) as the bindings 2.2.0 bump** so the
parser un-blinding and the columns land together and no capture gap needs a
backfill later.

**In scope:**

- vehicle_positions: `wheelchair_accessible` (unpopulated fleet-wide today, but
  a free scalar — day-one capture when the first agency publishes it)
- trip_updates: `license_plate`, `arrival_scheduled_time`,
  `departure_scheduled_time`, `departure_occupancy_status`, stop_time_properties
  (`assigned_stop_id`, `stop_headsign`, `pickup_type`, `drop_off_type`),
  trip_properties (`trip_id`, `start_date`, `start_time`, `shape_id`,
  `trip_headsign`, `trip_short_name` — prefixed `trip_properties_*`),
  modified_trip (`modifications_id`, `affected_trip_id`, `start_date`,
  `start_time` — prefixed `modified_trip_*`)
- service_alerts: `cause_detail`, `effect_detail`, `tts_header_text`,
  `tts_description_text`, `image_url`, informed_entity `direction_id`, and
  `active_periods_json` — the full active-period list JSON-encoded (the #91
  granularity decision: 172 fleet alerts carry >1 period, max 251; a JSON
  column preserves them without multiplying rows; `active_period_start`/`end`
  keep first-period compatibility)
- Raise the root `gtfs-realtime-bindings` floor to `>=2.2.0` (per #93 review) +
  a descriptor regression test
- `tf/bigquery.tf` external-table schemas updated to match
- DESIGN.md schema lists updated (incl. the pre-existing `occupancy_percentage`
  omission)

**Out of scope** (and where it lands):

- Multi-language translations: **keep-first is the decision**, recorded on #91 —
  whole-language capture would double text storage for a display-layer concern.
- `multi_carriage_details` (repeated message, zero fleet publishers): decide
  granularity when someone publishes it (#91).
- Backfilling historical partitions: separate decision on #91; raw retention
  gives it a 365-day horizon. Old parquet simply lacks the columns (BigQuery
  external tables read them as NULL; DuckDB consumers need `union_by_name`).

## Implements

No `specs/` yet (#87). Governing intent: issue #91 (census-verified add-list),
# 86 (columnset is a cross-repo contract — uniform superset, nullable
everywhere).

## Approach

1. `schemas.py`: append nullable fields to the three schemas (types mirror
   existing conventions: enums int32, epoch times int64/uint64, text string).
2. `compaction.py` extractors: populate the new keys, reusing the existing
   `get_text` first-translation helper for the new translated fields;
   `active_periods_json` via `json.dumps` of `[{start, end}, ...]` whenever
   any period exists.
3. `tf/bigquery.tf`: matching columns (INT64/STRING) on the three RT tables —
   deploy's `tofu apply` picks them up.
4. Tests: extend `tests/dagster/test_compaction.py` with synthetic messages
   carrying every new field; descriptor guard pinning the 2.2.0-only fields;
   row-group identity unaffected (write-per-file unchanged).
5. `uv add --group dagster 'gtfs-realtime-bindings>=2.2.0'` (floor only; the
   lockfile already resolves 2.2.0).

## Validation

- [ ] Every census-populated field appears in schema + extractor + BigQuery DDL
- [ ] Extractor tests cover each new field with a synthetic FeedMessage,
      including a >1-active-period alert asserting `active_periods_json`
- [ ] Descriptor test pins `scheduled_time` / `cause_detail` / `image`
      visibility (fails on a bindings downgrade)
- [ ] Existing tests pass unmodified in their assertions about old columns
      (pure additions — no renames, no type changes)
- [ ] ruff, mypy strict, full pytest green
- [ ] DESIGN.md column lists match `schemas.py` exactly

## Risks / unknowns

- **Mixed-schema history**: partitions written before this release lack the new
  columns. BigQuery external tables tolerate missing Parquet columns as NULL;
  DuckDB consumers must read with `union_by_name`. Document on #86.
- **`active_periods_json` is a contract choice** — downstream consumers parse
  JSON for multi-period logic. Recorded here and on #91; revisit if a
  first-class nested type ever becomes worth the reader complexity.
- **uint64 timestamp fields**: existing convention uses uint64 for proto
  uint64s; BigQuery INT64 handles observed ranges.

## Notes

(Populated at closeout.)

## Follow-ups

(Populated at closeout.)
