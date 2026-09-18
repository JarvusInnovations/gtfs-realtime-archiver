---
status: done
depends: []
specs:
  - specs/architecture.md
issues: [110]
pr: 111
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
- [x] Post-deploy: `feed_discovery_sensor` ticks return to SUCCESS — v0.11.0
  deployed 2026-09-18; last FAILURE at 18:53:39 UTC, and every tick from
  18:58:40 onward is SUCCESS (1) or SKIPPED (20) across ~1h45m
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

- **Two validation criteria stay unchecked, deliberately.** The
  newly-added-agency criterion cannot be verified until the next agency is
  actually added — that is the whole point of it, and checking it off against
  KCM (whose partitions were registered by hand) would be circular. The KCM
  criterion is two-thirds done: all three partitions registered and all 27
  partitions (9 days x 3 feed types) materialized to parquet, verified by
  direct object checks. Only "appear on gtfsrt.io" is outstanding, because
  `bucket_inventory_schedule` regenerates `inventory.json` at 04:00 UTC daily;
  it closes on the next run with no further work.
- **SKIPPED, not SUCCESS, is this sensor's healthy steady state.** The first
  post-deploy tick returned SUCCESS (enqueueing run requests); every tick
  after returns SKIPPED, because run-key dedup means there is nothing new to
  request. A future reader checking sensor health should treat SKIPPED as
  good and FAILURE as the only alarm — "no SUCCESS ticks" is not a symptom.
- **The first successful tick causes a thundering herd.** Recovery enqueued
  ~50 runs at once — the sensor requests runs for every discovered feed for
  yesterday, and after a long outage every one of those run keys is new to
  it. Idempotent and it drains on its own (SUCCESS went 18 -> 118 within the
  hour), but a recovery after a *longer* outage would enqueue proportionally
  more. Worth knowing before restarting this sensor after a lengthy stop.
- **The error message points at the wrong component.** The daemon reports
  `DagsterUserCodeUnreachableError: Unable to reach the user code server`,
  which reads as a dead code server. The code server was healthy the entire
  time; the real cause was the sensor function exceeding its 60s budget, and
  that only appeared in a second, inner traceback. Check code-server health
  before believing the outer message.
- **Timing of the discovery, for the record**: the raw->parquet gap was
  visible for nine days in `inventory.json` (70 entries vs 74 configured
  feeds) before anyone looked. The four-feed diff was the fastest route to
  the bug and cost one command.
- **Two zombie runs** have sat in STARTED since 2026-05-10 and 2026-06-28.
  Unrelated to this work, but they will skew any "currently running" alarm
  built for #114.

## Follow-ups

- Issue [#114](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/114)
  — alert on chronic sensor/schedule tick failure, and on configured-but-
  missing feeds. This is the half of the bug that actually cost nine days:
  the timeout was an afternoon's fix, the silence is what made it expensive.
  Scoped out of this plan deliberately; see its Scope section.
- Issue [#92](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/92)
  — related but distinct layer. #92 reconciles raw `.pb` against parquet rows
  for *known* partitions; it would not have caught KCM, which had no
  partitions at all. Worth settling the boundary between the two when #114 is
  designed.
- None (tracked elsewhere): the Big Blue Bus `service_alerts` gap noted in
  Scope remains unresolved and unexplained. That feed returns valid protobuf
  with 0 entities, so its absence from `inventory.json` may be correct
  behaviour rather than a symptom. Left alone rather than guessed at.
