---
status: in-progress
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
- Closeout recommendations for **what metadata the pipeline should persist** to make
  this validation cheap and continuous — using the dashboard is partly an instrument
  for finding out (pre-declared candidates in Follow-ups)

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
│ 3. read parquet footers  │ data/  │    reconciliation.duckdb)   │
│    (gcsfs, footer reads) │ duckdb │ pages/index.md              │
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
`google-cloud-storage`, `gcsfs`, `pyarrow`, `duckdb` in the script header). Invoked
as `uv run --script dashboards/proto-parquet-reconciliation/extract.py` (with a
`#!/usr/bin/env -S uv run --script` shebang) — the explicit `--script` flag makes
the isolated environment a guarantee rather than an inference, even when run from
inside the project directory. The root `pyproject.toml` / `uv.lock` stay untouched —
no dashboard deps enter the main project. Requires ADC with read access to the **raw protobuf bucket**
(private); the same ADC identity covers the parquet footer reads — one credential
story for both sides.

Listing the raw bucket is the dominant cost — ~600k objects/day across all feeds,
counting both `.pb` and `.meta` sidecars (every fetch writes a pair, so ~300k
fetches/day; listing both suffixes is deliberate, since `proto_files_hourly`
records `meta_count`) — so the CLI flags cut it **server-side**, not client-side:

- `--feed-type` prunes at the top of the path (`{feed_type}/date=…`) — whole
  prefixes skipped.
- `--agency` resolves to the agency's feed base64urls and constructs **exact
  prefixes**: the hour partition value is fully deterministic
  (`hour={date}T{HH}:00:00Z`, `storage.py:61`), so every prefix is directly
  buildable as `{feed_type}/date={date}/hour={date}T{HH}:00:00Z/base64url={b64}/`.
  Plain `prefix=` listing — server-side by construction, no glob semantics to get
  wrong. (GCS `matchGlob` matches against full object names and has documented
  restrictions combined with `delimiter`; constructed prefixes sidestep both.)
- All listings — agency-filtered or full-range — fan out over the same unit,
  (feed_type, date, hour[, base64url]) prefixes, via a thread pool (~32 workers);
  listing is I/O-bound pagination and parallelizes cleanly.
- Regardless of filters, each hour prefix is first listed with `delimiter="/"`,
  which returns only the distinct `base64url=…/` sub-prefixes (no object names,
  ~one page each) — the complete set of feeds actually present in GCS. This keeps
  unmapped-feed detection working even in `--agency` mode, where the exact-prefix
  fan-out is otherwise structurally blind to feeds it didn't derive from
  feeds.parquet. Cost: ~24 × 3 × days extra near-empty calls.

Default date range: last 14 days. All agencies × 14 days ≈ 8.4M objects ≈ 8–9k
list pages ≈ 5–10 min with the fan-out (unmeasured estimate — the first validation
run calibrates it); full history is a batch job, not interactive.

1. **Feed dimension**: fetch `feeds.parquet` (public HTTP) →
   `feeds(base64url, url, feed_type, agency_id, agency_name, system_id, system_name)`.
   Feeds found in GCS but absent from feeds.parquet appear as `(unmapped)` rather
   than being dropped — an unmapped feed is itself a finding.
2. **Raw side**: list as above, capturing `size_bytes` per object (`list_blobs`
   returns it at no extra cost). Persist per-file rows →
   `raw_files(feed_type, date, hour, base64url, name, size_bytes)` — a few
   million rows is nothing for DuckDB, and the step-4 anti-join needs the names —
   and aggregate → `proto_files_hourly(feed_type, date, hour, base64url,
   pb_count, meta_count, zero_byte_count)`. Zero/implausibly-tiny `.pb` sizes
   separate the "never-valid HTTP-200 garbage" class without downloading
   anything.
3. **Parquet side**: list `{feed_type}/date={date}/` prefixes for the requested
   window only (same constructed-prefix approach, 3 feed_types × days calls) —
   existence and `size_bytes` per partition, **windowed by construction**. An
   unwindowed full-bucket pass would join every parquet date ever against a
   14-day raw side and manufacture "rows but no protos" discrepancies for the
   entire backlog. Path parsing copies the shape of `inventory.py`'s
   `_RT_PATTERN` (a PEP 723 script can't import from `src/`; copying the regex
   keeps the two definitions of a valid RT parquet path from drifting). Footers
   via `gcsfs` + `pyarrow.parquet.read_metadata` (~8KB range read, same mechanism
   as `inventory.py`) only for objects that exist. Partitions expected from the
   raw side but absent → row of nulls (missing-partition signal, don't skip). →
   `parquet_daily(feed_type, date, base64url, row_count, size_bytes,
   num_row_groups)`.
4. **Discrepancy escalation**: for the feed × dates the cheap signals flag
   (missing/zero-row parquet, or `num_row_groups` below the pb count), read the
   parquet's `source_file` column (`SELECT DISTINCT source_file`) and anti-join
   against `raw_files` → `parquet_source_files(feed_type, date, base64url,
   source_file)` — naming exactly which `.pb` files never made it into the
   parquet. For the anti-joined files only (a handful of GETs, not 600k), fetch
   the sibling `.meta`'s `response_code` / `content_length` and combine with
   `size_bytes` from `raw_files` to **label each drop automatically**: parse
   failure, never-valid fetch, or legitimately-empty feed.
5. Write all tables to `data/reconciliation.duckdb` (overwrite per run).

**Why `num_row_groups` + `source_file`**: compaction writes one row group per
successfully-parsed non-empty `.pb` file, so `pb_count − num_row_groups` ≈ files
dropped — a cheap first-pass signal readable from the ~8KB footer alone. But it is
a heuristic with known couplings: pyarrow splits a `write_table` call into multiple
row groups above its max-rows-per-group default, and a `.pb` that parses fine but
yields zero records (a legitimately empty feed) also produces no row group — a
different failure mode than a parse drop. The **exact** answer comes from the
non-nullable `source_file` column every RT schema carries (`schemas.py`): a
distinct-count matches contributing `.pb` files precisely, and the anti-join
(step 4) names the dropped files, with `.meta` + size classification labeling each
one automatically. Helpfully, the drop space is bounded: the only silent-drop
paths in `compact_single_feed` are the parse-failure `continue`
(`compaction.py:524`) and the zero-record `continue` (`:513`) — a failed download
raises and fails the whole partition — so a short parquet has exactly two
explanations. Strategy: footer heuristic for the broad scan, `source_file` column
reads only on discrepancy rows.

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
   - num_row_groups < pb-files-with-content → probable parse drops, with the
     anti-joined `.pb` names from `parquet_source_files` listed per candidate
   - feeds in raw bucket but `(unmapped)` in feeds.parquet

   Discrepancy queries restrict to the **reconcilable window**, with buffers at
   both edges rather than exact boundaries:

   - **Old edge**: exclude/annotate dates older than ~358 days, not exactly 365 —
     GCS lifecycle deletion is asynchronous and per-object, so days near the
     boundary can be *partially* reaped, reading as fake mid-day archiver gaps.
   - **New edge**: exclude/annotate dates less than 2 full days old — compaction
     *starts* at 02:00 UTC for the previous day (`schedules.py`) and can run for
     hours on busy feeds, so a same-morning extract would flag yesterday as
     missing while compaction is mid-run. Document an override for anyone
     deliberately reconciling up to the edge.

### Stage 3 — Runner & docs

README with the two-command flow (skip transit-lake's `bin/dashboard` launcher —
overkill for one temporary dashboard):

```bash
uv run --script dashboards/proto-parquet-reconciliation/extract.py --agency septa --start 2026-07-01
cd dashboards/proto-parquet-reconciliation && npm install && npm run sources && npm run dev
```

Re-run `npm run sources` after every extract — Evidence snapshots sources at build time.

Also in this stage:

- Pin `nodejs` in `.tool-versions` at a current LTS (22.x): Evidence needs ≥18
  and the vendored specops CLI needs ≥20, so one pin satisfies both (the repo
  currently pins python/uv/opentofu only).
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
- [ ] For at least one flagged discrepancy, the `source_file` anti-join names
      specific `.pb` files, spot-checked by downloading and parsing one
- [ ] Measured runtimes for all-agencies × 14 days and single-agency × 14 days
      recorded in Notes — the interactive-use *targets* (≲15 min / ≲2 min) live in
      Risks, because an unmeasured estimate can't be a pass/fail criterion
- [ ] Every discrepancy view restricts to the buffered reconcilable window
      (~358-day retention edge, 2-day compaction edge — excluded or annotated)
- [ ] Never-fetches principle checked mechanically: grep of `sources/` and
      `pages/` query blocks for `gs://`, `http(s)://`, `read_parquet`, `read_csv`
      finds no remote data access (empty result pasted into Notes at closeout;
      prose hyperlinks exempt)
- [ ] Closeout Notes/Follow-ups record concrete pipeline-metadata recommendations
      informed by actually using the dashboard
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
- [ ] Unmapped feeds (in GCS, not in feeds.parquet) surface rather than disappear —
      including in an `--agency`-filtered run (via the delimiter discovery pass)
- [ ] `data/`, `node_modules/`, `.evidence/` are gitignored; no data committed

## Risks / unknowns

- **Raw-bucket read access** — extract needs a viewer-capable identity on the
  `gtfs-archiver` project's raw bucket. Verify ADC works before building (the
  Dagster run-worker SA impersonation flow in `.claude/CLAUDE.md` is one option).
- **Listing cost/time** — linear in days × feeds; the hour-prefix fan-out and
  exact constructed prefixes are what keep the 14-day default interactive. Stage
  1's runtime numbers are unmeasured estimates — calibrate on the first validation
  run and revisit the fan-out width if they're off. Working targets (targets, not
  validation criteria): ≲15 min for all agencies × 14 days, ≲2 min single-agency.
- **Row-group heuristic coupling** — "one row group per non-empty parsed file" is
  an internal implementation detail of `compaction.py` with no test pinning it;
  record at closeout what would break it (row-group splitting, the zero-record
  `continue`). The `source_file` escalation path is the durable signal.
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

(Populated at closeout.)

- Tracked as: hardening issue to be written at closeout, informed by what the
  temporary version teaches us — centered on **what the pipeline should record so
  this validation becomes cheap and continuous**. Candidates already visible:
  - Compaction writes a per-feed × date **manifest** alongside `data.parquet`
    (files_listed, files_parsed, files_dropped + reasons, records_written). Today
    those numbers exist only as Dagster materialization metadata
    (`compaction.py`'s Output metadata) — trapped in the Dagster event DB, not
    queryable from the lake.
  - Aggregate fetch outcomes pipeline-side: `.meta` sidecars already carry
    `response_code` / `duration_ms` / `content_length` per fetch (`storage.py`),
    but reading them after the fact costs one GET per file (one per fetch,
    ~300k/day) — fold them into the manifest at compaction time, which downloads
    each `.meta` anyway.
  - Publish per-day inventory: `inventory.py` computes per-file row counts, then
    aggregates them away to `date_min`/`date_max`/`total_records` per feed.
  - Dagster asset checks over the manifest; hosted dashboard.
