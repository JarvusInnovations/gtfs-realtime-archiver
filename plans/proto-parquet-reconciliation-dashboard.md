---
status: done
depends: []
specs: []
issues: [77, 86]
pr: 90
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

- [x] `extract.py` for one agency × one day produces `pb_count` matching an
      independent listing of the same prefix (gcloud/gsutil/gcsfs — any
      implementation other than the extract's own listing path), and
      `row_count` matching a direct DuckDB query against the public parquet
      (validate the validator first). (Amended 2026-08-04: originally named
      `gcloud storage ls | wc -l` specifically; the CLI's credentials were
      expired during validation and the criterion's intent — an independent
      count — is method-agnostic. The gcsfs listing used is a fully separate
      client implementation from the extract's `google-cloud-storage` path.)
- [x] For one known-good feed × day, `num_row_groups` equals the number of `.pb`
      files that parse to ≥1 record (validates the parse-drop heuristic itself)
- [x] For at least one flagged discrepancy, the `source_file` anti-join names
      specific `.pb` files, spot-checked by downloading and parsing one
- [x] Measured runtimes for all-agencies × 14 days and single-agency × 14 days
      recorded in Notes — the interactive-use *targets* (≲15 min / ≲2 min) live in
      Risks, because an unmeasured estimate can't be a pass/fail criterion
- [x] Every discrepancy view restricts to the buffered reconcilable window
      (~358-day retention edge, 2-day compaction edge — excluded or annotated)
- [x] Never-fetches principle checked mechanically: grep of `sources/` and
      `pages/` query blocks for `gs://`, `http(s)://`, `read_parquet`, `read_csv`
      finds no remote data access (empty result pasted into Notes at closeout;
      prose hyperlinks exempt)
- [x] Closeout Notes/Follow-ups record concrete pipeline-metadata recommendations
      informed by actually using the dashboard
- [x] No dashboard *runtime* dependencies enter the root project — extract deps
      live only in the script's PEP 723 inline metadata. (Amended 2026-08-04:
      originally "root `pyproject.toml`/`uv.lock` untouched"; review round 11
      motivated an executable fixture test of the dashboard SQL, which needs
      `duckdb` in a dev group — a test-only dependency, which the original
      wording would have forbidden for no benefit.)
- [x] `README.md` and `.claude/CLAUDE.md` Repository Layout updated for
      `dashboards/` in the commit that adds it
- [x] Dashboard renders with agency dropdown filtering every chart and table
- [x] Hourly heatmap shows pb counts by date × hour (UTC-labeled)
- [x] Daily comparison chart shows pb_count vs row_count per day
- [x] Discrepancy table lists dates with raw data but missing parquet, cross-checked
      by hand against at least one known-good and (if one exists) one known-missing
      partition
- [ ] Unmapped feeds (in GCS, not in feeds.parquet) surface rather than disappear —
      including in an `--agency`-filtered run (via the delimiter discovery pass)
- [x] `data/`, `node_modules/`, `.evidence/` are gitignored; no data committed

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

**Measured performance** (validation record, 2026-07-29–08-04):

- Single-agency (SEPTA) × 14 days: **76.5s wall, ~1.5GB peak RSS** (target ≲2 min: met)
- All-agencies × 14 days: **24.3 min wall, 7.8GB peak RSS** (target ≲15 min: missed —
  attributable to 18,235 real classification downloads; the fleet was dirtier than
  estimated, and later rounds cut that workload: contentful-only candidates,
  `.meta` counted-not-stored, `--max-classify-total`). Numbers predate those
  optimizations; treat as upper bound.
- Cross-checks: `pb_count` 4,311 = 4,311 (gcsfs independent listing);
  `row_count` 996,384 exact (direct DuckDB read of public parquet); row-group
  identity `pb == groups + classified drops` held with **zero residual** on all
  six SEPTA partitions; never-fetches grep returned empty.

**What the dashboard found while being validated** (it paid for itself pre-merge):

- Three SEPTA trip_updates `.pb` truncated mid-transfer at exact 4096-byte
  multiples, HTTP 200 — invisible in all existing metadata (fetch-side items on #92).
- The fleet-wide 41-byte parse-failure signature is the Clever Devices BusTime
  gateway body `ERROR: no connectivity to BusTime server!` served with HTTP 200
  (confirmed byte-identical at madison, dayton, missoula, bigbluebus).
- bigbluebus service_alerts 2026-07-23: first real #77 missing partition —
  **unrecoverable by design** (its only contentful file was the BusTime body);
  remediation loop exercised end-to-end including prod Dagster UI, and drove the
  diagnosis column + low-volume missing-partition classification features.
- 2026-07-23 05:47–08:26 UTC: fleet-wide archiver degradation to ~30% throughput
  (~12k snapshots lost) with 2 error lines logged — silent tenacity retries +
  silent APScheduler skips (misfire_grace_time=5s, max_instances=1); alerting
  items on #92. Only the heatmap caught it.
- **`valid_dropped = 0` fleet-wide**: across the 14-day window, compaction never
  dropped a file containing actual data; every shortfall is vendor garbage (438
  parse failures) or entity-empty padding (5,417).
- Alerts field census (via this extract's `raw_files` as sampling frame): 172
  alerts (5.1%) carry >1 active_period (max 251, MTA) — evidence on #91.

**As-built deviations from the Approach:**

- Sources are six (`feeds`, `proto_files_hourly`, `daily_comparison`,
  `drop_summary`, `dropped_files`, `extract_meta`), not the planned four;
  `parquet_daily.sql` was never created (reached through `daily_comparison`).
  `raw_files` / `meta_counts` / `parquet_daily` stay DuckDB-only tables.
- nodejs pinned in `dashboards/proto-parquet-reconciliation/.tool-versions`
  (asdf nearest-ancestor), not the root — keeps Node out of every CI job's
  tool install.
- `unpack.py` (manual proto-vs-parquet inspection to `.scratch/`) grew out of
  investigation needs; not in original scope, now load-bearing for forensics.
- Tests exist despite the "temporary tooling untested" framing: drift guards
  (`tests/test_reconciliation_contracts.py`) plus an executable SQL fixture
  suite (`tests/test_reconciliation_dashboard_sql.py`) asserting the page's
  diagnosis/remediate strings. Both live in `tests/` and run in CI; the
  dashboard itself is still outside ruff/mypy paths, and **CI never builds the
  Evidence project** — `npm run sources`/`build` can rot silently. Stated
  decision, acceptable for temporary tooling.
- Dedupe of feeds must live **at ingest** (`FEEDS_INGEST_SQL`): Evidence source
  queries run standalone against the raw tables and cannot reference each
  other — a source-level dedupe protects nothing (review round 12).

**Gotchas worth remembering** (several cost real time):

- Evidence types an all-NULL column DOUBLE in materialized parquet — cast
  nullable strings explicitly in sources.
- `npm run build` while `npm run dev` is serving clobbers the dev template.
- pyarrow `combine_chunks()` on a `string` column overflows int32 offsets past
  2GB (one day of SEPTA trip_updates source_file exceeds it); dedupe per chunk,
  read dictionary-encoded.
- A script named `inspect.py` shadows stdlib `inspect` and breaks its own deps.
- Python block-buffers stdout when redirected: background extracts need `-u`
  or flushed progress prints.
- DuckDB leaves an orphan `.wal` after an interrupted run; unlink both.
- `HEADER_ONLY_MAX = 20` sits one byte under the ~21-byte minimum
  entity-carrying message; a real drop at ≤20 bytes would hide in
  `header_only_count` (fails safe: hides, never invents).
- `compaction.read_meta_file` derives the sidecar via `.replace(".pb", ".meta")`
  (replaces every occurrence) vs the scripts' `rsplit` — agree on
  archiver-generated names; fix belongs with #92's invariant tests.
- The row-group identity's upper bound: pyarrow splits a `write_table` above
  ~1Mi rows; largest observed snapshot ~18k rows (~50× headroom).
- Declined optimizations, deliberately: row-group-statistics shortcut for
  `source_file` reads (would delete the column-read stage; unnecessary once
  escalation was cheap), incremental Arrow ingestion during listing,
  `--skip-discovery`, `agency_id` interpolation sanitization (repo-controlled slug).

## Follow-ups

- Issue [#91](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/91) —
  dropped-field audit + bindings adoption, born from this work: the census ran
  on this extract's `raw_files` sampling frame and is substantively complete
  for all three feed types (see issue comments); remaining work is the schema
  additions and two granularity decisions (multi `active_period`,
  multi-language translations). Bindings 2.2.0 merged as PR #93; deploys with
  release v0.9.3.
- Tracked as: one validation criterion remains unchecked — "unmapped feeds
  surface rather than disappear". The mechanism (delimiter discovery pass,
  `(unmapped)` rows with decoded URLs) is implemented and structurally
  verified, but all 71 fleet feeds were mapped throughout validation, so the
  end-to-end behavior was never observed with a real unmapped feed. Closes
  itself the first time one appears (or can be forced by temporarily removing
  a feed from `agencies.yaml`'s feeds.parquet export).
- Issue [#92](https://github.com/JarvusInnovations/gtfs-realtime-archiver/issues/92) —
  hardening: pipeline-side recording (compaction manifest, fetch-side truncation
  detection, per-day inventory), asset checks + Cloud Monitoring alerting,
  invariant tests in `tests/`, and the record/alert/explore/forensics layering
  that decides where each piece lives. Written mid-plan (not at closeout) once
  real usage had produced the findings it needed. Superseded candidates list
  kept below for the record:
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
  - Fetch-side truncation detection (found live by this dashboard, 2026-07-29:
    three SEPTA trip_updates `.pb` truncated at exact 4096-byte multiples with
    response_code 200): `.meta` records `content_length = len(received)`, not
    the server's `Content-Length` header, so truncation is invisible in
    metadata, and parse outcomes exist only as Dagster log warnings. Record the
    header value and/or a `parse_ok` flag in `.meta` at fetch time.
