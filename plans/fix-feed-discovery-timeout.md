---
status: in-progress
depends: []
specs:
  - specs/architecture.md
issues: [110]
---

# Plan: Fix feed discovery timeout (prefix listing, not object scan)

## Scope

Bring `feed_discovery_sensor` back into conformance with
`specs/architecture.md`'s Feed discovery rule: discovery must read the prefix
hierarchy only, so its cost tracks the number of feeds rather than the volume
of archived data, and it stays inside the daemon's per-tick budget.

In scope: `discover_feed_urls()` in
`src/dagster_pipeline/defs/assets/compaction.py`, a regression test pinning
prefix-only listing, and the spec amendment stating the budget rule. Also in
scope as operational remediation: registering the stranded King County Metro
partitions and backfilling the days they missed.

Out of scope: raising the daemon's 60s gRPC tick timeout (treats the symptom);
alerting on chronic sensor-tick failure (follow-up — the silence is arguably
the worse half of this bug); the Big Blue Bus `service_alerts` gap, which is
confounded by that feed being empty.

## Implements

- `specs/architecture.md` — Feed discovery: prefix-hierarchy-only reads, and
  the rule that discovery degrades in latency, never in completeness.

## Approach

Root cause (#110): `discover_feed_urls()` pages **every object** under
`{feed_type}/date={date}/` and regexes each blob name. A single hour of
`vehicle_positions` holds ~6,700 objects (measured 2026-09-18), so a day is
~161k for that type alone, ×3 types, every 5 minutes. The sensor tick blew
through 60s and failed on all 300 of the last ticks.

The failure is silent by construction: the sensor is the *only* path that
registers a feed into the `*_feeds` dynamic partitions, while the daily
schedules iterate only already-registered partitions. So existing feeds keep
compacting and `/health/feeds` stays green while every newly-added feed is
dropped. King County Metro archived raw protobufs for nine days with no
parquet and no inventory entry.

Fix: walk the two prefix levels (`date=` → `hour=` → `base64url=`) with
`delimiter="/"` listings via a `_list_prefixes()` helper, which exhausts the
iterator before reading `.prefixes` (the client only populates it after the
pages are consumed). Cost becomes ~1 + 24 requests per feed type regardless of
data volume.

Regression test: a mock bucket that serves prefix listings and **raises** on
any listing without a delimiter, pinning the contract that discovery never
pages objects.

## Validation

- [x] Regression tests pass and fail against the pre-fix implementation
  (verified via `git stash`: all 3 fail with "discovery paged objects")
- [x] `uv run ruff check src/ tests/`, `uv run mypy src/`,
  `uv run pytest tests/` all pass
- [ ] Post-deploy: `feed_discovery_sensor` ticks return to SUCCESS
- [ ] Post-deploy: a newly-added agency reaches `inventory.json` without
  manual partition registration
- [ ] KCM partitions registered and 2026-09-09..2026-09-17 backfilled; the
  three feeds appear on gtfsrt.io

## Risks / unknowns

- **Hour-prefix format is load-bearing.** Discovery now depends on the layout
  being `date=` → `hour=` → `base64url=`. `generate_storage_path()` owns that
  shape; a change there silently empties discovery rather than erroring. The
  regression test pins the parsing but not the writer's format.
- **Empty-hour fan-out.** Cost is one request per hour prefix per feed type.
  Fine at 24 hours/day, but a future finer-grained partition (minute-level)
  would reverse the win.
- **Backfill volume.** Nine days × 3 KCM feeds is 27 partitions; trip_updates
  days are the expensive ones.

## Notes

To be populated at closeout.

## Follow-ups

To be populated at closeout.
