---
status: done
depends: [rt-field-extraction]
specs: []
issues: [95, 96, 97]
pr: 99
---

# Entity-type capture: trip_modifications, shapes, stops

Close the last parsing gap: the 2026-08-04 fleet census (PR #94) found Madison
Metro and Big Blue Bus publishing `trip_modifications` (312), `shape` (316),
and `stop` (90) entities inside their trip_updates feed URLs — fetched,
archived to raw `.pb`, then silently dropped at compaction. Three new Parquet
tables + BigQuery external tables capture them. Targets the v0.9.3 release as
pass two behind PR #94 (branch stacked on `feat/rt-field-extraction`; user
decision 2026-08-05: #94 is overburdened, this ships as its own PR, both merge
before the release).

## Scope

- `schemas.py`: three new schemas — `trip_modifications`, `shapes`, `stops`
- `compaction.py`: three extractors; `compact_single_feed` generalized to a
  multi-table one-pass loop; the trip_updates asset becomes a `@multi_asset`
  emitting four outputs from one parse of the same raw files (#95 recorded
  decision — download+parse are the dominant costs and are already paid)
- `REQUIRED_BINDINGS_FIELDS` guard entries for every new HasField surface
  (FeedEntity.shape/stop/trip_modifications raise ValueError on pre-2.2
  bindings — exactly the swallowed-into-`files_failed` class the guard exists
  to catch at import instead)
- Manifest test: the three `DROPPED_SUBTREES` entries are deleted; every leaf
  of the three subtrees gets a capture disposition
- `sensors.py` / `schedules.py`: TU config selects all four asset keys (a
  non-subsettable multi_asset cannot be selected by one key)
- `tf/bigquery.tf`: three external tables, order-locked to schemas.py (DDL
  parity test extended)
- DESIGN.md: new-table schema sections, grain rationale, duplication note,
  join-key documentation; plus the separate grain-decision honesty entry
  (user-directed, same PR)

Out of scope: multi-language handling for service_alerts (#98 — but see the
stops decision below); reconciliation-dashboard expectations for the new
tables (local-only tooling, noted in Risks); historical backfill (#91 open
question, unchanged).

## Design decisions

1. **Grain: one row per FeedEntity — the message grain.** Repeated structures
   are JSON-encoded columns, unnesting happens downstream at query/transform
   time. This deliberately does NOT extend the one-row-per-innermost-element
   denormalization of the original three tables. That grain was set in
   e4a11e1 without articulated rationale (see the DESIGN.md entry this plan
   adds), and its cost is now visible: schema decisions frozen at compaction
   where changes are expensive, vs a transform layer (dbt) where they are
   cheap. New tables with no compatibility baggage get the conservative
   shape: capture the message whole, derive downstream.
   - `trip_modifications`: `selected_trips_json`, `start_times_json`,
     `service_dates_json`, `modifications_json` (selectors, delays,
     replacement_stops, service_alert_id, last_modified_time nested inside).
     Join keys still work at this grain: `entity_id` is what
     `modified_trip_modifications_id` (TU/VP rows) points at;
     `service_alert_id` is reachable via JSON functions until a transform
     layer unnests it.
   - `shapes`: naturally entity-grain — `shape_id`, `encoded_polyline`.
   - `stops`: scalars per-field-presence; six TranslatedString fields as
     `*_translations_json` (below).
2. **Stop TranslatedStrings: full-fidelity JSON from day one.**
   `[{"text": …, "language": …}, …]` per field, column NULL when unset —
   never `"[]"` (the `active_periods_json` conventions). #97 says the #98
   decision should apply from day one; capturing everything is the
   #98-neutral superset: no selection rule is baked in, any future keep-first
   or prefer-English column is derivable downstream, and no migration can
   ever be owed. The manifest records the `.translation.language` leaves
   under stops as CAPTURED (unlike service_alerts' keep-first drops).
3. **JSON conventions** (inherited from `active_periods_json` /
   `_carriages_json`): column NULL when the repeated field is empty, never
   `"[]"`; compact separators; per-field presence inside objects (unset →
   JSON null); nested empty lists inside objects stay `[]` (honest: the
   parent exists, its list is empty).
4. **Every-snapshot duplication accepted** (#96/#97): polylines and stop
   definitions repeat identically in every ~20s snapshot for a detour's
   lifetime. Matches the VP/TU every-snapshot model; zstd + dictionary
   encoding collapse repeats on disk; dedup belongs at query time
   (`QUALIFY ROW_NUMBER() OVER (PARTITION BY shape_id ...)`), documented in
   DESIGN.md.
5. **One `@multi_asset`, four outputs, not subsettable.** Re-materializing a
   partition rewrites all four outputs — correct, same source bytes. The
   zero-records → no-upload path applies per output (29 of 31 TU feeds have
   none of these entities; their shapes/stops/trip_modifications outputs
   upload nothing). Parse failures count once into a shared `files_failed`
   reported on every output; a conversion/write failure names file AND table
   and fails the whole partition (all four outputs).
6. **Output prefixes**: `trip_modifications/`, `shapes/`, `stops/` in the
   parquet bucket, same `date=`/`base64url=` hive layout — read prefix stays
   `trip_updates/` in the protobuf bucket.

## Validation criteria

- [x] Manifest test passes with zero `DROPPED_SUBTREES` entity types — every
      leaf of the three subtrees dispositioned to a real column
- [x] Extraction tests: populated fixtures (no NULLs, Arrow round-trip),
      unset → NULL, JSON encodings exact, record↔schema key parity
- [x] compact tests: multi-output run writes four parquets from mixed-entity
      feeds; zero-entity tables upload nothing; error contract (parse-skip /
      dg.Failure naming file+table / close semantics) preserved
- [x] DDL parity test covers all six tables; targeted `tofu plan`
      shows 3 creates, 0 destroys, no changes to existing tables (verified
      2026-08-05)
- [x] ruff + mypy strict + full pytest green (239 tests)
- [x] End-to-end against real Madison/BBB data (2026-08-04 snapshots,
      read-only): Madison 34 trip_modifications / 34 shapes / 11 stops per
      snapshot, BBB 4/4/0; live service_alert_id join keys (`CWDetour-…`)
      and multi-week service_dates captured; Arrow round-trip clean.
      Producer quirk recorded: BBB publishes last_modified_time in
      MILLISECONDS (1785797872000) — captured verbatim in JSON, consumers
      beware
- [x] DESIGN.md: grain-decision entry + new-table sections; README table list

## Risks / unknowns

- ~~**BigQuery external-table creation over an empty GCS prefix** may fail at
  deploy-time `tofu apply`.~~ **Disproven 2026-08-05**: a probe external
  table with CUSTOM hive partitioning + explicit schema created cleanly over
  the empty `trip_modifications/` prefix (0 objects) and was deleted; the
  deploy-time apply cannot hit this. No pre-seeding needed.
- **Reconciliation dashboard** (local-only) counts rows per `.pb` for TU
  feeds; TM/shape/stop rows come from the same protos and must not be
  reconciled against TU expectations. Not release-blocking; noted for the
  next dashboard run.
- **Run-worker memory**: four streaming writers instead of one for TU
  partitions. Buffers hold compressed bytes; Madison worst case is small
  (hundreds of entities/snapshot). Watch item on #92, not a blocker.
- The experimental spec status of these messages means a future bindings bump
  can reshape them — the manifest test fails closed when that happens.
