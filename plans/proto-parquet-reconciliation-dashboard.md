---
status: planned
depends: []
specs: []
issues: [77, 86]
---

# Plan: Proto → Parquet Reconciliation Dashboard (Evidence)

## Scope

A **temporary local** [Evidence](https://evidence.dev) dashboard for validating the
protobuf → parquet compaction pipeline: count of archived `.pb` files by hour by day
compared with rows in the resulting daily parquet, filterable by agency. Lives in a
new `dashboards/proto-parquet-reconciliation/` directory. It belongs in this repo
(not gtfsrt-sandbox) because the raw side needs private-bucket ADC access that the
credential-free public sandbox deliberately lacks, and the row-group heuristic
couples to `compaction.py` internals — this is producer-side QA whose findings feed
back into archiver changes.

**In scope:**

- Python extract script (GCS → local DuckDB) with `--agency` / `--start` / `--end` /
  `--feed-type` CLI args
- Evidence project with agency dropdown filtering the whole dashboard
- Hourly proto-count heatmap, daily proto-vs-rows comparison, discrepancy tables

**Out of scope** (and where it lands):

- Pipeline changes (persisting per-file counts, per-day inventory) — future work
  informed by this dashboard; contracts groundwork in #86
- Hardening into a permanent hosted capability — a follow-up issue to be written
  *after* the temporary version has been used (see Follow-ups)
- Automating the #77 backfill — this dashboard only *detects* missing partitions

## Implements

No `specs/` directory exists in this repo yet. Governing intent comes from:

- Issue #77 — audit parquet output for missing feed/day partitions (the discrepancy
  views partially automate the detection half)
- transit-lake's dashboard principle (adopted here): **the dashboard visualizes
  prepared data and never fetches or computes** — a stale view means the data build
  is stale, not that the dashboard is broken

## Approach

Modeled on transit-lake `dashboards/gtfs-rt-quality-metrics/` (Evidence project +
DuckDB source reading locally-prepared data, read-only).

```
extract.py (uv, ADC creds)          Evidence project (npm)
┌──────────────────────────┐        ┌─────────────────────────────┐
│ 1. feeds.parquet (HTTP)  │        │ sources/archiver/*.sql      │
│ 2. list raw pb bucket    │──────▶ │   (duckdb connection to     │
│ 3. read parquet footers  │ data/  │    data/reconciliation.db)  │
│    (gcsfs, footer reads) │ *.db   │ pages/index.md              │
└──────────────────────────┘        │   (Dropdown: agency)        │
                                    └─────────────────────────────┘
```

### Directory layout

```
dashboards/proto-parquet-reconciliation/
├── README.md             # run instructions
├── extract.py            # data prep: GCS → data/reconciliation.duckdb
├── data/                 # gitignored — extract output
├── package.json          # evidence + core-components + duckdb source only
├── evidence.config.yaml  # borrowed from transit-lake (theme/appearance)
├── .gitignore            # node_modules, .evidence, data/
├── sources/archiver/
│   ├── connection.yaml        # duckdb → data/reconciliation.duckdb
│   ├── feeds.sql              # feed/agency dimension
│   ├── proto_files_hourly.sql # pb counts per feed × date × hour
│   └── parquet_daily.sql      # rows/size/row-groups per feed × date
└── pages/index.md        # the dashboard
```

### Stage 1 — `extract.py`

A **self-contained uv script** using PEP 723 inline metadata (declaring
`google-cloud-storage`, `gcsfs`, `pyarrow`, `duckdb` in the script header). uv runs
inline-metadata scripts in an isolated environment, so
`uv run dashboards/proto-parquet-reconciliation/extract.py` works from the repo root
while the root `pyproject.toml` / `uv.lock` stay untouched — no dashboard deps enter
the main project. Requires ADC with read access to the **raw protobuf bucket**
(private); the same ADC identity covers the parquet footer reads — one credential
story for both sides.

Listing the raw bucket is the dominant cost (~600k objects/day across all feeds),
so the CLI flags cut it **server-side**, not client-side:

- `--feed-type` prunes at the top of the path (`{feed_type}/date=…`) — whole
  prefixes skipped.
- `--agency` resolves to the agency's feed base64urls and lists with
  `list_blobs(match_glob="date={date}/**/base64url={b64}/*.pb")` per feed —
  base64url sits *below* hour in the path, but `match_glob` pushes the filter into
  GCS so a per-agency run returns only that agency's objects instead of paging the
  whole date prefix.
- Full-range listings fan out over (feed_type, date, hour) prefixes via a thread
  pool (~32 workers) — listing is I/O-bound pagination and parallelizes cleanly.

Default date range: last 14 days. All agencies × 14 days ≈ 8.4M objects ≈ 8–9k
list pages ≈ 5–10 min with the fan-out (unmeasured estimate — the first validation
run calibrates it); full history is a batch job, not interactive.

1. **Feed dimension**: fetch `feeds.parquet` (public HTTP) →
   `feeds(base64url, url, feed_type, agency_id, agency_name, system_id, system_name)`.
   Feeds found in GCS but absent from feeds.parquet appear as `(unmapped)` rather
   than being dropped — an unmapped feed is itself a finding.
2. **Raw side**: list as above (names only); parse `hour=` and `base64url=` from
   paths. →
   `proto_files_hourly(feed_type, date, hour, base64url, pb_count, meta_count)`.
3. **Parquet side**: per feed × date, read the footer of
   `{feed_type}/date={date}/base64url={b64}/data.parquet` via `gcsfs` +
   `pyarrow.parquet.read_metadata` (~8KB range read — same mechanism as
   `inventory.py`). Missing object → row of nulls (missing-partition signal, don't
   skip). → `parquet_daily(feed_type, date, base64url, row_count, size_bytes,
   num_row_groups)`.
4. Write all tables to `data/reconciliation.duckdb` (overwrite per run).

**Why `num_row_groups`**: compaction writes one row group per successfully-parsed
non-empty `.pb` file, so `pb_count − num_row_groups` ≈ files dropped by parse
failures — the silent-loss signal the pipeline currently discards. Heuristic only:
a `.pb` that parses fine but yields zero records (a legitimately empty feed) also
produces no row group — a different failure mode than a parse drop — so label the
metric accordingly in the UI.

### Stage 2 — Evidence project

Scaffold manually copying transit-lake's minimal shape; trim `package.json` to
`@evidence-dev/evidence`, `@evidence-dev/core-components`, `@evidence-dev/duckdb`
(skip the ~10 unused datasource plugins transit-lake carries). Source SQL files
materialize the three tables plus a pre-joined `daily_comparison`.

`pages/index.md` components:

1. **Agency `<Dropdown>`** fed from `feeds`, referenced as `${inputs.agency.value}`
   in every query — natively supported, so the dropdown is the primary mechanism.
   Optional second dropdown: feed_type.
2. **Hourly heatmap** (date × hour, pb_count summed over the agency's feeds) —
   archiver coverage gaps jump out. Evidence has no first-class heatmap: use
   `<Heatmap>` if the installed core-components version has it, else stacked
   BarChart or ECharts custom chart — decide at build time.
3. **Daily comparison**: pb_count vs row_count per day (orders of magnitude apart —
   dual y-axes or normalized companion metric).
4. **Rows-per-proto trend** per feed per day — should be near-constant; steps/spikes
   flag extraction or feed-content changes.
5. **Discrepancy tables** (the validation payoff):
   - pb_count > 0 with missing/zero-row parquet → missing partitions (#77)
   - num_row_groups < pb-files-with-content → probable parse drops
   - feeds in raw bucket but `(unmapped)` in feeds.parquet

   Discrepancy queries exclude (or annotate) dates older than the raw bucket's
   365-day retention — raw objects age out while parquet persists, which would
   read as false discrepancies in a full-history run.

### Stage 3 — Runner & docs

README with the two-command flow (skip transit-lake's `bin/dashboard` launcher —
overkill for one temporary dashboard):

```bash
uv run dashboards/proto-parquet-reconciliation/extract.py --agency septa --start 2026-07-01
cd dashboards/proto-parquet-reconciliation && npm install && npm run sources && npm run dev
```

Re-run `npm run sources` after every extract — Evidence snapshots sources at build time.

Also in this stage:

- Pin `nodejs` in `.tool-versions` (Evidence needs Node ≥18; the repo currently
  pins python/uv/opentofu only).
- Update `README.md` and `.claude/CLAUDE.md`'s Repository Layout for the new
  top-level `dashboards/` directory, in the same commit that adds it.
- `extract.py` deliberately sits outside CI's `ruff check src/ tests/` /
  `mypy src/` paths — temporary tooling, not held to the repo's strict-mypy bar;
  state this in the dashboard README.

## Validation

- [ ] `extract.py` for one agency × one day produces `pb_count` matching
      `gcloud storage ls | wc -l` on the same prefix, and `row_count` matching a
      direct DuckDB query against the public parquet (validate the validator first)
- [ ] For one known-good feed × day, `num_row_groups` equals the number of `.pb`
      files that parse to ≥1 record (validates the parse-drop heuristic itself)
- [ ] Extract for all agencies × 14 days completes in interactive time (≲15 min)
- [ ] Single-agency extract over 14 days completes in ≲2 min, confirming
      `match_glob` filtering happens server-side rather than client-side
- [ ] Root `pyproject.toml` / `uv.lock` are untouched — extract deps live only in
      the script's PEP 723 inline metadata
- [ ] `README.md` and `.claude/CLAUDE.md` Repository Layout updated for
      `dashboards/` in the commit that adds it
- [ ] Dashboard renders with agency dropdown filtering every chart and table
- [ ] Hourly heatmap shows pb counts by date × hour (UTC-labeled)
- [ ] Daily comparison chart shows pb_count vs row_count per day
- [ ] Discrepancy table lists dates with raw data but missing parquet, cross-checked
      by hand against at least one known-good and (if one exists) one known-missing
      partition
- [ ] Unmapped feeds (in GCS, not in feeds.parquet) surface rather than disappear
- [ ] `data/`, `node_modules/`, `.evidence/` are gitignored; no data committed

## Risks / unknowns

- **Raw-bucket read access** — extract needs a viewer-capable identity on the
  `gtfs-archiver` project's raw bucket. Verify ADC works before building (the
  Dagster run-worker SA impersonation flow in `.claude/CLAUDE.md` is one option).
- **Listing cost/time** — linear in days × feeds; the hour-prefix fan-out and
  server-side `match_glob` are what keep the 14-day default interactive. Stage 1's
  runtime numbers are unmeasured estimates — calibrate on the first validation run
  and revisit the fan-out width if they're off.
- **Raw `.pb` count is an upper bound on valid fetches** — HTTP-200 garbage (HTML
  error pages, truncated bodies) is archived and only dies at parse time; the
  row-group heuristic is what separates "parse-dropped" from "never valid".
- **Heatmap component availability** in the pinned Evidence version — fallback
  documented in Approach.
- **Node ≥18 required** by Evidence — `.tool-versions` pins no nodejs today;
  Stage 3 adds the pin.

## Notes

(Populated at closeout.)

## Follow-ups

(Populated at closeout. Already known: write the hardening issue — persist per-file
counts in the pipeline instead of client-side rescans, publish a per-day inventory,
Dagster asset checks, hosted dashboard — informed by what the temporary version
teaches us.)
