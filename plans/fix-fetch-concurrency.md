---
status: in-progress
depends: []
specs:
  - specs/behaviors/archiving.md
issues: [104]
---

# Plan: Fix system-wide fetch serialization (per-feed APScheduler tasks)

## Scope

Bring the archiver's fetch concurrency into conformance with
`specs/behaviors/archiving.md` ("At most one concurrent run per feed; feeds
fail independently"): fetches for *different* feeds must run concurrently up
to the `MAX_CONCURRENT` semaphore, while runs of the *same* feed never
overlap.

In scope: `src/gtfs_rt_archiver/scheduler.py` task registration; a regression
test pinning cross-feed concurrency. Out of scope: stagger algorithm changes
(#60), scheduler-health dashboards (#59), and any interval/misfire tuning —
those follow once the fleet's real concurrent load profile is observable.

## Implements

- `specs/behaviors/archiving.md` — the Scheduling section's per-feed
  concurrency rule and the Fetching section's `MAX_CONCURRENT` ceiling (which
  is currently inert because nothing else ever runs in parallel).

## Approach

Root cause (#104): all feeds' schedules register the same module-level
callable, and APScheduler v4 derives **Task** identity from the callable —
so every feed shares one Task whose `max_running_jobs` defaults to 1,
serializing the whole fleet.

Fix: register a **per-feed task** before each `add_schedule` call —
`configure_task(f"fetch-{feed.id}", func=_execute_scheduled_fetch,
max_running_jobs=1)` — and point the schedule at that task id. The
`max_running_jobs=1` cap then means what the spec means: one concurrent run
*per feed*, with cross-feed parallelism governed by the fetch pool's
semaphore.

Regression test: real `AsyncScheduler`, N feeds with stagger patched to 0 so
all schedules fire together, fetch job that tracks peak concurrency while
sleeping; assert peak ≥ 2 (pre-fix behavior pins it at exactly 1).

## Validation

- [ ] Regression test demonstrates cross-feed concurrency > 1 under
  simultaneous triggers (and fails against the pre-fix scheduler)
- [ ] Per-feed no-overlap is still enforced (max_running_jobs=1 per feed task)
- [ ] `uv run ruff check src/ tests/`, `uv run mypy src/`,
  `uv run pytest tests/` all pass
- [ ] Post-deploy (next release): fetch-latency/misfire metrics reviewed and
  #51 (Big Blue Bus) re-checked for recovered vehicle positions

## Risks / unknowns

- **Thundering herd on restart** — parallel fetches were previously
  impossible, so the md5 stagger has never been load-bearing. After this fix
  a restart still spreads first fires across each feed's interval; watch the
  fetch-duration histogram after deploy (#59 dashboards would make this
  visible; #60 improves the spread).
- **Downstream rate limits** — some agencies may throttle now that fetches
  can actually overlap in time across their feeds; per-feed intervals are
  unchanged, so sustained request rate is the same, only phase changes.

## Notes

(Populated at closeout.)

## Follow-ups

(Populated at closeout.)
