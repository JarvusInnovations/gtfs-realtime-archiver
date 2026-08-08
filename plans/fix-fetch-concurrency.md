---
status: done
depends: []
specs:
  - specs/behaviors/archiving.md
issues: [104]
pr: 106
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

- [x] Regression test demonstrates cross-feed concurrency > 1 under
  simultaneous triggers (and fails against the pre-fix scheduler — verified
  via `git stash`: peak pinned at 1 until timeout)
- [x] Per-feed no-overlap is still enforced (max_running_jobs=1 per feed task)
- [x] `uv run ruff check src/ tests/`, `uv run mypy src/`,
  `uv run pytest tests/` all pass (246 passed)
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

- The post-deploy validation criterion is intentionally unchecked at merge:
  it can only close after the next release ships this fix to Cloud Run. It
  closes out via #51 (re-check Big Blue Bus) and the #59 dashboard work.
- APScheduler subtlety worth remembering: `add_schedule(callable, id=...)`'s
  `id` names the *schedule*; Task identity (which `max_running_jobs` scopes
  to) comes from `callable_to_ref(callable)` unless a task is configured
  explicitly. `configure_task(task_id, func=...)` + `add_schedule(task_id,
  ...)` is the pattern for distinct tasks sharing one callable.
- The bug made the md5 stagger non-load-bearing (serialization was the real
  spacing); post-fix, stagger is what actually spreads load — raising #60's
  priority.

## Follow-ups

- Issue [#51](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/51)
  — re-check for recovered vehicle positions after the next release deploys
  this fix.
- Issue [#60](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/60)
  — rank-based staggering is now genuinely load-bearing (see Notes).
- Issue [#59](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/59)
  — scheduler-health dashboard is where the post-deploy criterion gets
  verified.
