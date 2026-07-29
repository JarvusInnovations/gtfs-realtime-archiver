# Proto → Parquet Reconciliation Dashboard

A **temporary local** [Evidence](https://evidence.dev) dashboard for validating the
protobuf → parquet compaction pipeline: raw `.pb` counts by hour/day compared
against compacted parquet row counts, filterable by agency. Detects missing
feed/day partitions ([#77](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/77))
and classifies files dropped during compaction.

Plan: [`plans/proto-parquet-reconciliation-dashboard.md`](../../plans/proto-parquet-reconciliation-dashboard.md)

## Prerequisites

- **ADC with read access to the raw protobuf bucket** (`protobuf.gtfsrt.io` is
  private; the parquet bucket is public):

  ```bash
  gcloud auth application-default login
  ```

- **uv** (runs the extract script; deps are declared inline via PEP 723 — the
  repo's root `pyproject.toml`/`uv.lock` are not touched)
- **Node ≥18** (pinned in the repo's `.tool-versions`)

## Usage

Two-command flow, from the repo root:

```bash
# 1. Extract: GCS → data/reconciliation.duckdb (default: last 14 days, all agencies)
uv run --script dashboards/proto-parquet-reconciliation/extract.py --agency septa --start 2026-07-01

# 2. Serve the dashboard
cd dashboards/proto-parquet-reconciliation && npm install && npm run sources && npm run dev
```

Re-run `npm run sources` after every extract — Evidence snapshots sources at
build time.

`extract.py --help` documents the flags (`--agency`, `--feed-type`, `--start`,
`--end`, `--output`). `--agency` limits *extraction cost* via exact server-side
prefixes; the dashboard's dropdown handles display filtering.

**Each run overwrites its output DB** (default `data/reconciliation.duckdb`) —
use `--output` to extract somewhere else without destroying the previous
extract. Don't run two extracts against the same output concurrently.

**Manual deep inspection**: `unpack.py --pb <raw .pb path>` (the dashboard's
dropped-files table generates the command per row) downloads five consecutive
snapshots, parses them to JSON, and extracts the matching parquet rows to
`.scratch/inspect/` for side-by-side review.

## Notes

- `data/` is gitignored; no extracted data is ever committed.
- This dashboard **visualizes prepared data and never fetches or computes** — a
  stale view means the extract is stale, not that the dashboard is broken.
- `extract.py` is temporary tooling and deliberately sits outside CI's
  `ruff check src/ tests/` / `mypy src/` paths — it is not held to the repo's
  strict-mypy bar.
