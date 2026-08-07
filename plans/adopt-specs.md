---
status: in-progress
depends: []
specs:
  - specs/README.md
  - specs/principles.md
  - specs/architecture.md
  - specs/behaviors/archiving.md
  - specs/behaviors/compaction.md
  - specs/behaviors/published-artifacts.md
issues: [87, 86]
---

# Plan: Adopt specs/ (spec-driven development pathway)

## Scope

Execute the adoption pathway defined in issue #87: seed the `specs/` skeleton,
land the first real specs (the published data contracts from #86 and the
already-resolved compaction/archiving behavior currently living as normative
prose in DESIGN.md), wire the spec-drift auditor, and demote DESIGN.md to
background/rationale so exactly one place is normative.

In scope:

- `specs/README.md`, `specs/principles.md`, `specs/architecture.md` (skeleton)
- `specs/behaviors/published-artifacts.md` — the cross-repo contract surfaces
  from #86 (inventory.json, schedules.json, feeds.parquet, parquet Hive layout,
  raw protobuf layout, retention, public access, consumers, change process)
- `specs/behaviors/compaction.md` — grain, per-table schemas, presence and JSON
  semantics, complete-capture policy, failure/overwrite rules (extracted from
  DESIGN.md's normative prose)
- `specs/behaviors/archiving.md` — raw capture behavior (scheduling, fetch and
  retry taxonomy, storage writes, sidecars, sharding, health/metrics surface)
- `.claude/agents/spec-drift-auditor.md` + `.claude/commands/audit-spec-drift.md`
  (wired from the specops skill references, methodology customized to this repo)
- DESIGN.md shrunk to background/rationale/history with pointers into `specs/`
- `.claude/CLAUDE.md` and README.md documentation rules updated accordingly

Out of scope:

- Reciprocal contract documentation in consumer repos (transit-lake,
  gtfsrt-sandbox) — #86 remains open for that
- Inline load-bearing-contract comments at producing code sites (#86 item 2,
  partially addressed by existing comments in inventory.py)
- Specs for behavior still under active investigation (#91/#92 reconciliation
  hardening, #101 extensions) — those follow the spec-first flow as that work
  lands
- `specops hook install` (optional pathway step 6) — left to a separate
  decision since it touches every contributor session

## Implements

- `specs/README.md` / `specs/principles.md` / `specs/architecture.md` — new;
  descriptive of the system as built (code already conforms)
- `specs/behaviors/published-artifacts.md` — the #86 contract table; conforms
  to `inventory.py`, `feeds_metadata.py`, `schedule.py`, `storage.py`,
  `tf/storage.tf` as shipped
- `specs/behaviors/compaction.md` — conforms to `compaction.py`, `schemas.py`,
  `partitions.py`, `schedules.py`, `sensors.py` and the field-coverage manifest
  tests as shipped
- `specs/behaviors/archiving.md` — conforms to the archiver service
  (`scheduler.py`, `fetcher.py`, `storage.py`, `health.py`, `metrics.py`)

## Approach

1. Author the plan (this file) and the spec set on one branch — this is the
   "one bounded feature" specops mode: spec change and plan in tandem.
2. Extraction, not invention: every normative statement in the new specs is
   sourced from the current implementation or DESIGN.md's recently-updated
   contract prose; where the two disagree, the implementation wins and the
   discrepancy is noted (DESIGN.md's known drift: compaction "Data Flow"
   described runtime feed discovery — actual mechanism is the
   feed_discovery_sensor + per-type dynamic partitions with three per-type 2am
   schedules materializing known partitions).
3. Copy the auditor agent + command from
   `.agents/skills/specops/references/`, customizing Phase 3 (implementation
   inventory) to this repo's layout.
4. Shrink DESIGN.md: keep problem statement, design goals/non-goals, rationale
   tables, the denormalized-grain retrospective, dependency justification, and
   migration appendices; replace moved content with pointers.
5. Update `.claude/CLAUDE.md` (specs-lead rule, documentation-maintenance
   rules, repo layout) and README.md's documentation pointer.

## Validation

- [ ] `specs/` skeleton exists (README, principles, architecture) following the
  specops layout and template conventions
- [ ] Every contract surface in #86's table appears in
  `specs/behaviors/published-artifacts.md` with path, shape, cadence,
  retention, stability rule, and known consumers
- [ ] The normative semantics formerly in DESIGN.md (grain, presence
  conventions, JSON encoding, complete-capture policy, failure and overwrite
  rules) appear in `specs/behaviors/compaction.md`; DESIGN.md retains no
  normative contract content
- [ ] `/audit-spec-drift` command and `spec-drift-auditor` agent are wired and
  reference this repo's actual directories
- [ ] `.claude/CLAUDE.md` documentation-maintenance rules name `specs/` as
  normative and the specops section no longer says "specs/ does not exist yet"
- [ ] `uv run ruff check src/ tests/`, `uv run mypy src/`, and
  `uv run pytest tests/` pass (no code changes expected)

## Risks / unknowns

- **Spec drift at birth** — extracting from prose risks codifying stale
  statements. Mitigated by sourcing every claim from code read during
  authoring, not from DESIGN.md alone.
- **Two-normative-sources window** — until DESIGN.md is shrunk in the same PR,
  both documents could claim authority. Mitigated by landing the whole change
  as one PR.
- **Consumer list accuracy** — #86's consumer table is a point-in-time code
  search (2026-07-16); the spec records it as "known consumers as of" rather
  than a closed set.

## Notes

(Populated at closeout.)

## Follow-ups

(Populated at closeout.)
