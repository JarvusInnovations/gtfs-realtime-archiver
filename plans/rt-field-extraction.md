---
status: done
depends: [proto-parquet-reconciliation-dashboard]
specs: []
issues: [91]
pr: 94
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
- Raise the `gtfs-realtime-bindings` floor to `>=2.2.0` in the `dagster`
  dependency group (per #93 review; only compaction parses protos —
  `dagster-deploy` inherits via include-group) + a descriptor regression test
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

No `specs/` yet (#87). Governing intent: issue #91 (census-verified add-list)
and issue #86 (columnset is a cross-repo contract — uniform superset,
nullable everywhere).

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

- [x] Every census-populated field appears in schema + extractor + BigQuery DDL
- [x] Extractor tests cover each new field with a synthetic FeedMessage,
      including a >1-active-period alert asserting `active_periods_json`
- [x] Descriptor test pins `scheduled_time` / `cause_detail` / `image`
      visibility (fails on a bindings downgrade)
- [x] Existing tests pass unmodified in their assertions about old columns
      (pure additions — no renames, no type changes)
- [x] ruff, mypy strict, full pytest green
- [x] DESIGN.md column lists match `schemas.py` exactly

## Risks / unknowns

- **Mixed-schema history**: partitions written before this release lack the new
  columns. BigQuery external tables tolerate missing Parquet columns as NULL;
  DuckDB consumers must read with `union_by_name`. Document on #86.
- **`active_periods_json` is a contract choice** — downstream consumers parse
  JSON for multi-period logic. Recorded here and on #91; revisit if a
  first-class nested type ever becomes worth the reader complexity — the
  concrete alternative is `list<struct<start, end>>`, which lands as a
  BigQuery REPEATED RECORD and gives native `UNNEST` instead of JSON string
  parsing for "is this alert active at time T" queries.
- **uint64 timestamp fields**: existing convention uses uint64 for proto
  uint64s; BigQuery INT64 handles observed ranges.

## Notes

- Verification beyond the checklist: three-way schema ↔ extractor ↔ BigQuery
  parity checked programmatically (27/42/28 columns incl.
  `image_alternative_text`, names AND types machine-checked in CI); proto2 `HasField`
  semantics confirmed empirically on bindings 2.2.0 (explicit enum 0 captured,
  unset → NULL); targeted `tofu plan` shows all three external tables
  **update in-place, 0 to destroy**; 213 tests.
- **String-presence decision** (post-review): fields of sparse-by-design
  nested messages (`trip_properties_*`, `modified_trip_*`, `assigned_stop_id`,
  `stop_headsign`) use per-field `HasField` — unset is NULL, never `""` (a
  parent-only guard would poison the IS NOT NULL queries these columns exist
  for, and unset `stop_headsign` means "inherit the scheduled headsign").
  Strings on routinely-populated parents (`license_plate` et al.) keep the
  existing parent-presence convention. Documented in DESIGN.md; pinned by
  tests.
- **`image_alternative_text` added** (accessibility sibling of the `tts_*`
  fields — capturing it was cheaper than justifying its absence);
  `LocalizedImage.media_type`/`language` stay keep-first-dropped alongside the
  translation deferral.
- **Naming decision**: service_alerts' informed-entity `direction_id` keeps the
  bare name, matching the existing SA convention (`agency_id`/`route_id`/
  `stop_id` already carry EntitySelector semantics distinct from the same
  names' trip-descriptor meanings in VP/TU). Documented in DESIGN.md; recorded
  here because cross-feed-type unions must know it.
- Deviations from Approach: tests landed in a new
  `tests/dagster/test_field_extraction.py` rather than extending
  `test_compaction.py`; the promised "row-group identity unaffected" test was
  not written — the write-per-file loop is unchanged and the real
  invariant-pinning test remains #92's item. `compact_single_feed` **was**
  touched (review round 3): the Arrow conversion moved outside the per-file
  parse handler (`pyarrow.ArrowInvalid` subclasses `ValueError`), so a
  schema/type bug now fails the partition loudly — naming the offending file —
  instead of masquerading as per-file parse warnings over an empty partition.
  That flips conversion-error semantics from skip-and-warn to fail-partition;
  noted on #92 so the invariant test pins the new behavior.
- New enum fields all use per-field `HasField` guards; the pre-existing
  unguarded `trip.schedule_relationship` reads (the #93 review's enum-0
  hazard) were left as-is — fleet probe found no unknown values in flight, and
  changing existing column semantics belongs to its own change if ever.

## Follow-ups

- Issue [#91](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/91) —
  remaining after this plan: the historical-backfill decision (365-day raw
  retention horizon; re-materializing partitions populates the new columns for
  history), and the two recorded deferrals (multi-language translations =
  keep-first; `multi_carriage_details` = decide when a publisher appears).
- Issue [#86](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/86) —
  the columnset contract docs must include the new columns, the
  pre-release-NULL / `union_by_name` mixed-schema note, and the SA
  informed-entity naming semantics.
