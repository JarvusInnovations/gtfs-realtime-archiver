# Specs

This directory is the **normative source of truth** for what should be true of
this system. Implementation follows spec: before changing behavior, change the
spec here, get it accepted, then bring the code into conformance. Spec↔code
divergence is a bug, not debt.

The full methodology is the **specops** skill vendored at
[`.agents/skills/specops/`](../.agents/skills/specops/SKILL.md). Work-in-flight
that bridges these specs to merged code is tracked in [`plans/`](../plans/).
[`DESIGN.md`](../DESIGN.md) is *not* normative — it holds background, rationale,
and history only.

## Layout

```
specs/
├── README.md                          # This file — conventions and index
├── principles.md                      # Project-wide decisive principles
├── architecture.md                    # Components, data flow, foundational decisions
└── behaviors/
    ├── archiving.md                   # Raw capture: scheduling, fetch, retry, storage writes
    ├── compaction.md                  # Protobuf → parquet: grain, schemas, semantics, failure rules
    └── published-artifacts.md         # Cross-repo data contracts and their consumers
```

## Conventions

- **Declarative, testable statements.** Specs say *what* must be true, not how
  to implement it. A reviewer must be able to compare running software against
  a spec line and decide conformance without interpretation.
- **Principles are decisive.** `principles.md` and per-spec `## Principles`
  sections hold rules that pick a side of a real trade-off. A statement that
  rules nothing out doesn't belong there.
- **Merged specs are implemented or planned.** Every spec on `develop`/`main`
  either matches the implementation or is claimed by a committed plan's
  `specs:` frontmatter. Specs still being designed live on a draft planning PR,
  not here.
- **A spec change ripples to its plans.** After editing a spec, check
  `grep -l '<spec-path>' plans/*.md` and update the plans that implement it.
- **Published contracts evolve additively** — see
  [principles.md](principles.md#published-artifacts-evolve-additively) and
  [behaviors/published-artifacts.md](behaviors/published-artifacts.md) before
  touching anything external consumers read.

## Auditing

Run `/audit-spec-drift` (the `spec-drift-auditor` agent) for an exhaustive
comparison of this directory against the implementation. It reports specified-
but-not-implemented gaps, implemented-but-not-specified behavior, and direct
conflicts.
