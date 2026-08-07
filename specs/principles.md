# Principles

The project's philosophy, written down as decisive rules. Each picks a side of
a real trade-off so an implementer can resolve an unspecified case the way the
project would. Behavior specs reference the entries that bite on them.

## Archive verbatim, transform later

The archiver stores exactly the bytes each feed returned — it never parses,
validates, or normalizes GTFS-RT content. Compaction *extracts* fields; it
never interprets, selects, or repairs them: display selection (which language
to show), deduplication of repeated snapshots, and preference rules are
downstream transform concerns (dbt / query time), never capture concerns. When
a capture decision is in tension, capture more and decide later — full-fidelity
JSON companions beat a lossy scalar convenience column.

## Missed data has no value late

A GTFS-RT snapshot is only useful at the moment it's current. A missed fetch
tick is dropped, never queued or retried after the fact; scheduling favors
staying current over being complete. This is why there is no persistent fetch
queue, no work-distribution broker, and no backfill of raw snapshots.

## Published artifacts evolve additively

Everything under `gs://parquet.gtfsrt.io` is a public contract with external
consumers (see [behaviors/published-artifacts.md](behaviors/published-artifacts.md)).
Evolution adds — new columns, new keys, new tables, new paths. Renaming,
removing, or changing the semantics of an existing column, key, or path is a
breaking change and requires checking every known consumer first. New columns
populate from their ship-date forward; consumers are expected to read
schema-tolerantly (BigQuery external tables read absent columns as NULL;
DuckDB consumers use `union_by_name`).

## Derived data outlives raw

Raw protobuf expires at 365 days; parquet is kept indefinitely. An upstream
lifecycle expiry must never destroy or degrade derived output: a compaction
re-run over expired raw data no-ops (leaving the existing parquet untouched)
rather than overwriting it with an empty result.

## Complete capture or a recorded reason

Every leaf field of an archived entity type either maps to an output column or
carries an explicitly recorded drop reason, enforced in CI by the field-coverage
manifest tests. Silent field loss is a bug; a bindings upgrade that introduces
new fields must fail CI until each is dispositioned. (Known bounded exception:
proto2 producer extensions, tracked as a recorded blind spot — #101.)

## Fail loud on total failure, tolerate partial failure

One corrupt input must not sink a partition: per-file parse failures are
skipped and counted (`files_failed`). But a run that produces nothing from
non-empty input must fail the partition, never report a green zero-record
run — success with zero records is reserved for genuinely empty input.

## Secrets never reach storage paths or published artifacts

Storage paths encode the base feed URL only (auth query parameters excluded)
so paths stay stable across secret rotations and never leak credentials.
Nothing written to either bucket — objects, sidecars, inventories — may
contain a secret value.
