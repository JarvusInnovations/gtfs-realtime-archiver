# Behavior: Compaction (raw protobuf → parquet)

## Rule

For each `date|feed` partition, compaction parses every raw `.pb` snapshot for
that feed and UTC day and rewrites the feed's parquet output(s) for that day
wholesale. Extraction is complete and mechanical: every proto field maps to a
column or a recorded drop reason, and no interpretation, selection, or
normalization happens at this layer.

## Applies to

`src/dagster_pipeline/defs/assets/compaction.py` (extraction and writing),
`schemas.py` (authoritative column registry), `partitions.py` / `schedules.py`
/ `sensors.py` (orchestration — see
[architecture.md](../architecture.md#orchestration-model-dagster)), and the
field-coverage manifest tests (`tests/dagster/test_field_coverage.py`).

## Inputs and processing

- Input set: all `.pb` objects under the feed's `hour=*` prefixes for the
  partition date in the protobuf bucket.
- Each file is parsed once; `fetch_timestamp` is read from the adjacent `.meta`
  sidecar (NULL when the sidecar is missing or invalid — capture is never
  blocked on a sidecar).
- Output: one `data.parquet` per table under
  `{feed_type}/date=…/base64url=…/` (zstd), per the
  [published layout](published-artifacts.md#rt-parquet-tables-hive-layout).

## Failure handling

- **Per-file parse failure** (DecodeError/ValueError): skip the file, count it
  in `files_failed`, continue. One corrupt snapshot must not sink a partition.
- **Every file failed to parse** (non-empty input, zero parseable files): fail
  the partition with `dg.Failure`. A green zero-record run is reserved for
  genuinely empty input
  ([principle](../principles.md#fail-loud-on-total-failure-tolerate-partial-failure)).
- **Parquet conversion/write failure**: fail the partition loudly, naming the
  offending output; never upload a partial buffer.
- **Zero-record table**: uploads nothing — no file, never an empty file.

## Re-materialization and overwrite semantics

- Re-materializing a partition re-reads raw and rewrites all of that
  partition's outputs from scratch. While the raw `.pb` files are within their
  365-day retention, this **backfills any columns added since** the partition
  was last materialized — column presence therefore varies per partition with
  re-run history (consumers use `union_by_name` / schema-tolerant reads).
- Past the raw retention window the re-run finds no input and **no-ops**:
  success with zero records, existing parquet left untouched. A lifecycle
  expiry must never destroy derived data
  ([principle](../principles.md#derived-data-outlives-raw)).

## Tables and grain

| Table | Grain |
| --- | --- |
| `vehicle_positions` | one row per VehiclePosition entity per snapshot |
| `trip_updates` | one row per stop_time_update (or one base row if the update has none) |
| `service_alerts` | one row per informed_entity (or one base row if the alert has none) |
| `trip_modifications` | one row per entity per snapshot; repeated structures JSON-encoded |
| `shapes` | one row per entity per snapshot |
| `stops` | one row per entity per snapshot |

- The original three tables use the denormalized
  one-row-per-innermost-repeated-element grain (rationale and retrospective in
  DESIGN.md); the entity-grain tables added later (#95–#97) deliberately use
  one-row-per-entity with repeated structures JSON-encoded whole, so unnesting
  is a downstream transform concern.
- `trip_updates`, `trip_modifications`, `shapes`, and `stops` are the four
  outputs of **one non-subsettable multi-asset**: producers publish
  trip_modifications/shape/stop entities inside trip_updates feed URLs, so all
  four extract from a single download+parse pass of the trip_updates raw
  files. Re-materializing the partition rewrites all four outputs.
- Duplication across snapshots is by design (a detour's polyline repeats in
  every ~20s snapshot; zstd + dictionary encoding collapse it on disk). Dedup
  is query-time, e.g.
  `QUALIFY ROW_NUMBER() OVER (PARTITION BY shape_id ORDER BY feed_timestamp DESC) = 1`.

## Column registry

Every table carries the provenance columns `source_file`, `feed_url`,
`feed_timestamp`, `fetch_timestamp`, `entity_id` plus the header/entity columns
`feed_version`, `incrementality`, `is_deleted`. The complete per-table column
lists live in `schemas.py`; the field-coverage manifest tests enforce that the
registry stays complete against the proto descriptors
([principle](../principles.md#complete-capture-or-a-recorded-reason)). Notable
column families:

- **vehicle_positions**: trip descriptor (`trip_id`, `route_id`,
  `direction_id`, `start_date`, `start_time`, `schedule_relationship`),
  vehicle descriptor (`vehicle_id`, `vehicle_label`, `license_plate`,
  `wheelchair_accessible`), position (`latitude`, `longitude`, `bearing`,
  `odometer`, `speed`), status (`current_stop_sequence`, `stop_id`,
  `current_status`, `timestamp`, `congestion_level`, `occupancy_status`,
  `occupancy_percentage`), `modified_trip_*`, `multi_carriage_details_json`.
- **trip_updates**: same trip/vehicle descriptors, update-level (`trip_delay`,
  `trip_timestamp`), `trip_properties_*`, `modified_trip_*`, and per-row
  stop-time fields (`stop_sequence`, `stop_id`, arrival/departure
  delay/time/uncertainty/scheduled_time, `departure_occupancy_status`,
  `stop_schedule_relationship`, `assigned_stop_id`, `stop_headsign`,
  `pickup_type`, `drop_off_type`).
- **service_alerts**: alert scalars (`cause`, `effect`, `severity_level`,
  `cause_detail`, `effect_detail`, `url`, `header_text`, `description_text`,
  `tts_*`, `image_*`), first active period (`active_period_start`/`_end` — the
  first period **as published**, not chronologically first) plus
  `active_periods_json`, `communication_periods_json`, `impact_periods_json`,
  informed-entity fields, and the `*_translations_json` companions (#98).
- **trip_modifications**: `selected_trips_json`, `start_times_json`,
  `service_dates_json`, `modifications_json`.
- **shapes**: `shape_id`, `encoded_polyline` (Google encoded polyline).
- **stops**: `stop_id`, `stop_lat`, `stop_lon`, `zone_id`, `parent_station`,
  `stop_timezone`, `wheelchair_boarding`, `level_id`, and translated fields as
  `*_translations_json` only.

Join keys across tables: `trip_modifications.entity_id` is the target of
`modified_trip_modifications_id`; `Modification.service_alert_id` (inside
`modifications_json`) references service_alerts entity ids;
`selected_trips.shape_id` and `trip_properties_shape_id` reference
`shapes.shape_id`.

## Naming semantics

- Within `service_alerts`, bare informed-entity names (`agency_id`,
  `route_id`, `stop_id`, `direction_id`) carry **EntitySelector** semantics —
  distinct from the trip-descriptor meanings the same names have in
  vehicle_positions/trip_updates.
- In `trip_updates`, StopTimeProperties fields (`assigned_stop_id`,
  `stop_headsign`, `pickup_type`, `drop_off_type`) are deliberately bare: each
  row *is* a stop_time_update, so STU-level fields take row-level names; only
  fields hoisted from trip-level messages carry a provenance prefix
  (`trip_properties_*`, `modified_trip_*`). (`pickup_type`, `drop_off_type`,
  `stop_headsign` also name GTFS **static** `stop_times.txt` columns — qualify
  when joining static and RT.)
- Messages shared by VP and TU (`VehicleDescriptor`, `TripDescriptor`) are
  captured symmetrically; columnset differences between the two tables reflect
  feed-type-specific messages only.

## Presence semantics

**Strings.** Fields of sparse-by-design nested messages (`trip_properties_*`,
`modified_trip_*`, `assigned_stop_id`, `stop_headsign`) use per-field
presence: unset → NULL, never `""`. Strings on routinely-populated parents
(`vehicle_id`, `vehicle_label`, trip-descriptor strings) keep the
parent-presence convention: unset-on-present-parent reads `""`. One migrated
column: `license_plate` uses per-field presence in **both** tables from v0.9.3
onward; vehicle_positions partitions materialized earlier contain `""` for
present-descriptor/unset-plate rows (accepted on PR #94 over a permanent
cross-table asymmetry). Boundary-spanning reads normalize with
`NULLIF(license_plate, '')`; re-materialization rewrites old partitions under
the new convention while raw is alive.

**`feed_timestamp`** moved to per-field presence at v0.9.3 in the other
direction: an explicitly-published `header.timestamp = 0` records `0` where it
previously recorded NULL (pathological in practice; same per-partition re-run
boundary as `license_plate`).

**Enums.** All per-field-presence enums — `wheelchair_accessible`,
`pickup_type`, `drop_off_type`, `departure_occupancy_status`,
`incrementality`, `service_alerts.trip_schedule_relationship`,
`stop_schedule_relationship`, `cause`, `effect`, `severity_level`,
`current_status`, `congestion_level`, `occupancy_status` (exhaustive list) —
read NULL when unset and `0` when explicitly set to 0. **Exactly two
exceptions**: `vehicle_positions.schedule_relationship` and
`trip_updates.schedule_relationship` predate the convention and materialize
the proto default — unset reads `0` (= SCHEDULED, the spec's meaning for
unset). Note the split on the same proto field:
`service_alerts.trip_schedule_relationship` is the identical
`TripDescriptor.schedule_relationship` captured under the newer convention —
normalize with `COALESCE(trip_schedule_relationship, 0)` when unioning trip
descriptors across tables.

**Header/entity columns.** `feed_version` is free-form producer text
(unpopulated fleet-wide as of the 2026-08-04 census). `incrementality` uses
per-field presence; the extractors assume FULL_DATASET semantics, so a
non-zero value is the signal a DIFFERENTIAL feed appeared. `is_deleted` is
captured on payload-bearing entities; bare deletion tombstones carry no
payload and yield no row (relevant only to DIFFERENTIAL feeds).

## JSON column conventions

- JSON columns are STRING (BigQuery's native JSON type is unavailable for
  parquet external tables).
- A JSON column is NULL when its repeated field is empty — `"[]"` is never
  emitted at the top level. Inside objects, per-field presence applies (unset →
  JSON null); nested empty lists remain `[]` (the parent exists, its list is
  empty).
- Element order is publisher order.
- `active_periods_json` NULL means the alert is **always active** (spec
  semantics). The "active at time T" recipe has two load-bearing NULL rules: a
  NULL column means always-active (a plain UNNEST yields zero rows and
  silently reports the alert inactive — wrap the period check in `EXISTS`),
  and a JSON-null start/end means unbounded on that side. Use `EXISTS` so each
  alert row returns at most once (overlapping periods are legal and observed;
  `LEFT JOIN UNNEST … ON TRUE` fans out and inflates aggregations). BigQuery:

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

  (`JSON_VALUE` returns STRING; casts required.) DuckDB equivalent uses
  `json_transform(…, '[{"start":"UBIGINT","end":"UBIGINT"}]')` with the same
  `EXISTS` shape. `communication_periods_json` and `impact_periods_json` share
  this encoding, NULL semantics, and recipe.

## Translations

- Service_alerts scalar translated columns (`header_text` et al.) store the
  **first translation as published** — producer whim, not guaranteed English.
  Each has a full-fidelity `*_translations_json` companion
  (`[{"text","language"},…]` in publisher order, NULL when unset;
  `image_localized_images_json` is `[{"url","media_type","language"},…]`).
  Display-selection rules (prefer-`en`, skip `-html`) are downstream
  transform concerns
  ([principle](../principles.md#archive-verbatim-transform-later)).
- The stops table's translated fields ship as `*_translations_json` only —
  full fidelity from day one, no keep-first scalars.

## Complete-capture policy

Every leaf field of the archived entity types maps to a column or carries a
recorded drop reason, enforced by the descriptor-walk manifest tests: adding a
feed never requires a field audit, and a bindings bump that adds fields fails
CI until dispositioned. Columns added by a release populate from that release
onward; earlier partitions lack them until re-materialized (see
[re-materialization](#re-materialization-and-overwrite-semantics)).

**Scope caveat**: this covers the base schema only. Proto2 producer extensions
(e.g. MTA-NYCT's `nyct_subway.proto`) arrive as unknown fields that compaction
drops; they survive only in raw within its 365-day retention. Recorded blind
spot — #101.

## Untrusted content

Producer-supplied text columns (`header_text`, `description_text`, `tts_*`,
`cause_detail`, `effect_detail`, `image_url`, headsigns, …) are unvalidated
third-party content passed through verbatim. The pipeline never interprets or
fetches them; downstream renderers must treat them as untrusted input.

## Principles

**Inherited** — from [principles.md](../principles.md):

- [Archive verbatim, transform later](../principles.md#archive-verbatim-transform-later)
  — extraction is mechanical; selection and normalization live downstream.
- [Complete capture or a recorded reason](../principles.md#complete-capture-or-a-recorded-reason)
  — the manifest tests are this principle's enforcement.
- [Fail loud on total failure, tolerate partial failure](../principles.md#fail-loud-on-total-failure-tolerate-partial-failure).
- [Derived data outlives raw](../principles.md#derived-data-outlives-raw).
- [Published artifacts evolve additively](../principles.md#published-artifacts-evolve-additively)
  — every column and semantic in this file is consumer-visible contract.
