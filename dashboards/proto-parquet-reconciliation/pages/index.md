# Proto → Parquet Reconciliation

```sql extract_info
select
    start_date,
    end_date,
    coalesce(agency, 'all') as agency,
    coalesce(feed_type, 'all') as feed_type,
    extracted_at,
    window_old_days,
    window_new_days
from archiver.extract_meta
```

Extract window **<Value data={extract_info} column=start_date/> → <Value data={extract_info} column=end_date/>**
(agency: <Value data={extract_info} column=agency/>, feed type: <Value data={extract_info} column=feed_type/>),
extracted <Value data={extract_info} column=extracted_at/>.
Dates outside the buffered reconcilable window (older than
<Value data={extract_info} column=window_old_days/> days or newer than
<Value data={extract_info} column=window_new_days/> full days) are annotated,
not trusted — see the plan for why.

```sql agencies
-- scoped to feeds present in this extract (raw OR parquet side, matching
-- daily_comparison's spine), so picking an agency the extract never listed
-- can't render as a false all-clear
select distinct f.agency_id, f.agency_name
from archiver.feeds f
where f.agency_id is not null
    and f.base64url in (select base64url from archiver.daily_comparison)
order by f.agency_id
```

```sql feed_types
select distinct feed_type from archiver.daily_comparison order by 1
```

<Dropdown data={agencies} name=agency value=agency_id label=agency_name title="Agency">
    <DropdownOption value="%" valueLabel="All agencies"/>
</Dropdown>

<Dropdown data={feed_types} name=feed_type value=feed_type title="Feed type">
    <DropdownOption value="%" valueLabel="All feed types"/>
</Dropdown>

## Hourly archiver coverage

Raw `.pb` files archived per hour (UTC), over a complete date × hour spine —
a zero cell means the archiver wrote nothing that hour, including days-long
total outages (which would otherwise silently drop off the axis).

```sql hourly
with dates as (
    select unnest(generate_series(
        (select start_date::date from archiver.extract_meta),
        (select end_date::date from archiver.extract_meta),
        interval 1 day
    ))::date as date
),

hours as (select unnest(generate_series(0, 23, 1)) as hour)

select
    strftime(d.date, '%Y-%m-%d') as date,
    h.hour,
    coalesce(sum(p.pb_count), 0) as pb_count
from dates d
cross join hours h
left join archiver.proto_files_hourly p
    on p.date = d.date
    and p.hour = h.hour
    and ('${inputs.agency.value}' = '%' or coalesce(p.agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or p.feed_type = '${inputs.feed_type.value}')
group by all
order by 1, 2
```

<Heatmap
    data={hourly}
    x=date
    y=hour
    value=pb_count
    valueFmt="#,##0"
    title="Raw .pb files per hour (UTC)"
/>

## Daily comparison: protos archived vs rows compacted

Counts are orders of magnitude apart (one `.pb` yields many rows), so rows ride
the secondary axis.

```sql daily
select
    date,
    sum(pb_count) as pb_count,
    sum(row_count) as row_count
from archiver.daily_comparison
where ('${inputs.agency.value}' = '%' or coalesce(agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or feed_type = '${inputs.feed_type.value}')
group by all
order by date
```

<BarChart
    data={daily}
    x=date
    y=pb_count
    y2=row_count
    y2SeriesType=line
    yAxisTitle="raw .pb files"
    y2AxisTitle="parquet rows"
    title="Raw .pb files vs compacted parquet rows per day"
/>

## Rows per proto

Should be near-constant per feed; steps or spikes flag extraction or
feed-content changes. The focus dropdown narrows this chart only — the
page-level filters above still apply.

```sql rpp_names
select distinct display_name
from archiver.daily_comparison
order by display_name
```

<Dropdown data={rpp_names} name=rpp_feed value=display_name title="Focus feed">
    <DropdownOption value="%" valueLabel="All feeds"/>
</Dropdown>

```sql rows_per_proto
select
    date,
    display_name || ' · ' || feed_type as series,
    rows_per_proto
from archiver.daily_comparison
where ('${inputs.agency.value}' = '%' or coalesce(agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or feed_type = '${inputs.feed_type.value}')
    and ('${inputs.rpp_feed.value}' = '%' or display_name = '${inputs.rpp_feed.value}')
    and rows_per_proto is not null
order by date
```

<LineChart
    data={rows_per_proto}
    x=date
    y=rows_per_proto
    series=series
    title="Parquet rows per non-empty .pb file"
/>

## Discrepancies

### Missing partitions (#77)

*Contentful* raw `.pb` files exist but the daily parquet is missing. Feed-days
whose files are all zero-byte/header-only are excluded — compaction correctly
writes nothing for those (an always-quiet service_alerts feed is healthy, not
missing). Rows outside the reconcilable window, and rows where the extract's
own footer read failed, are labeled distinctly.

The **diagnosis** column answers "should I re-run compaction?" directly. For
low-volume missing partitions (≤25 contentful files) the extract downloads and
parses each one; if any parses with entities, real data was dropped and
remediation recovers it — but if all of them are invalid bytes (e.g. the
HTTP-200 `ERROR: no connectivity to BusTime server!` vendor bodies), no valid
data ever existed, remediation cannot help, and **no remediate command is
rendered**. The `remediate` column appears only when a re-run can actually
recover something (or when the partition was too busy to classify, where
re-running is safe to try): compaction rewrites the whole partition from raw,
so one run recovers anything still within raw retention.

**Preferred: remediate via the production Dagster UI** —
[dagster.gtfsrt.io](https://dagster.gtfsrt.io) → Assets → `{feed_type}_parquet`
→ Materialize → pick the date and feed partition from the row. The prod
instance already has the feed's dynamic partition key registered, and the run
executes as a Cloud Run Job under the run-worker SA with production env baked
in.

The `remediate` command is the local alternative. Its two halves: the first
registers the feed's dynamic partition key in your **local** Dagster instance
(production gets keys from `feed_discovery_sensor`; your `.dagster_home` has
its own registry, and a launch against an unregistered key fails with
`DagsterInvalidSubsetError`) — it loads `.env` itself and is idempotent. The
second is the `dg launch`. Caution: both halves inherit your `.env` — if it's
configured for docker-compose local dev (`STORAGE_EMULATOR_HOST`, `rt-protobuf`
bucket names), the launch will target the emulator, not production. Check
`.env` against `.env.example`'s production values first.

```sql missing_partitions
with base as (
    select
        *,
        -- mirrors compaction.url_to_partition_key (scheme strip, ~ prefix
        -- for http) — must not drift, or remediate commands stop resolving
        case
            when starts_with(url, 'http://') then '~' || substr(url, 8)
            else regexp_replace(url, '^https://', '')
        end as feed_key
    from archiver.daily_comparison
)

select
    b.feed_type,
    b.date,
    b.agency_name,
    b.system_name,
    b.pb_contentful_count,
    b.row_count,
    case
        when b.parquet_path is not null and b.row_count is null then 'footer read failed'
        when b.in_reconcilable_window then 'MISSING'
        else 'out of window'
    end as status,
    case
        when b.parquet_path is not null and b.row_count is null then ''
        when not b.in_reconcilable_window
            then 'outside reconcilable window — not classified'
        when d.classified is null
            then 'not classified (busy partition, or extract predates classification) — re-run is safe to try'
        when d.valid_dropped > 0
            then d.valid_dropped || ' valid file(s) dropped — remediation recovers real data'
        when d.errored > 0
            then 'classification incomplete (' || d.errored
                || ' errored) — unknown; re-run is safe to try'
        else 'no valid data ever existed (' || d.parse_failure || ' parse_failure, '
            || d.zero_entity
            || ' zero-entity) — do NOT re-run, nothing to recover'
    end as diagnosis,
    case
        when b.in_reconcilable_window and b.parquet_path is null and b.feed_key is not null
            -- shell-quote safety: this string gets pasted into a terminal.
            -- feed_key rides inside shell single-quotes in BOTH halves below,
            -- so a single quote is the only breaker — but exclude " and \\
            -- too, which would corrupt the inner Python string literals.
            and b.feed_key not like '%''%'
            and b.feed_key not like '%"%'
            -- contains() rather than LIKE: LIKE-escape semantics for
            -- backslash differ across engines and are easy to misread
            and not contains(b.feed_key, '\')
            and (d.classified is null or d.valid_dropped > 0 or d.errored > 0) then
            'uv run python -c ''from dotenv import load_dotenv; load_dotenv(); '
            || 'import dagster as dg; dg.DagsterInstance.get().add_dynamic_partitions("'
            || b.feed_type || '_feeds", ["'
            || b.feed_key || '"])'' && uv run dg launch --assets '
            || b.feed_type || '_parquet --partition '''
            || strftime(b.date, '%Y-%m-%d') || '|' || b.feed_key || ''''
    end as remediate
from base b
left join archiver.drop_summary d
    on d.feed_type = b.feed_type and d.date = b.date and d.base64url = b.base64url
where ('${inputs.agency.value}' = '%' or coalesce(b.agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or b.feed_type = '${inputs.feed_type.value}')
    and b.pb_contentful_count > 0
    and coalesce(b.row_count, 0) = 0
order by b.in_reconcilable_window desc, b.date, b.feed_type
```

<DataTable data={missing_partitions} rows=25 emptySet=pass emptyMessage="No missing partitions in this window 🎉">
    <Column id=feed_type/>
    <Column id=date/>
    <Column id=agency_name/>
    <Column id=system_name/>
    <Column id=pb_contentful_count/>
    <Column id=row_count/>
    <Column id=status/>
    <Column id=diagnosis wrap=true/>
    <Column id=remediate wrap=true/>
</DataTable>

```sql remediate_commands
select remediate
from ${missing_partitions}
where remediate is not null
order by date, feed_type
```

{#if remediate_commands.length > 0}

<Details title="Copy-ready remediation commands">

{#each remediate_commands as row}

- `{row.remediate}`

{/each}

</Details>

{/if}

### Probable drops during compaction

Partitions where row groups fall short of *contentful* `.pb` files (bigger than
a header-only message) — one row group is written per successfully-parsed
non-empty file, so the shortfall approximates dropped files. Restricted to the
reconcilable window, matching the extract's escalation.

**"Contentful" is a size heuristic; the explanation columns are ground truth**,
from parsing each shortfall file: `zero_entity` files parse fine but contain no
entities of this feed's type (bigger than a header, but nothing was lost —
common for feeds that pad empty responses), `parse_failure` files are invalid
bytes (real fetch-time loss), and `valid_dropped` files parse with entities yet
appear nowhere in the parquet — genuine compaction losses that should always
be zero.

```sql short_partitions
select
    c.feed_type,
    c.date,
    c.agency_name,
    c.system_name,
    c.pb_contentful_count,
    c.num_row_groups,
    c.pb_contentful_count - c.num_row_groups as shortfall,
    d.zero_entity,
    d.parse_failure,
    d.valid_dropped,
    d.errored,
    c.pb_contentful_count - c.num_row_groups
        - coalesce(d.classified, 0) as unclassified,
    c.row_count
from archiver.daily_comparison c
left join archiver.drop_summary d
    on d.feed_type = c.feed_type and d.date = c.date and d.base64url = c.base64url
where ('${inputs.agency.value}' = '%' or coalesce(c.agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or c.feed_type = '${inputs.feed_type.value}')
    and c.row_count > 0
    and c.num_row_groups < c.pb_contentful_count
    and c.in_reconcilable_window
order by coalesce(d.valid_dropped, 0) desc, coalesce(d.parse_failure, 0) desc, shortfall desc
```

<DataTable data={short_partitions} rows=25 emptySet=pass emptyMessage="No short partitions 🎉"/>

### Dropped `.pb` files, classified

Exact per-file attribution for flagged partitions: *contentful* files present
in the raw bucket whose path never appears in the parquet's `source_file`
column, labeled by parsing the file and reading its `.meta` sidecar. This
table corresponds 1:1 with the shortfall arithmetic above (extracts from
before 2026-07-31 also classified header-only files, which inflate the
`legitimately_empty_feed` count here until the next extract).

```sql drop_labels
select
    label,
    count(*) as files,
    min(size_bytes) as min_bytes,
    max(size_bytes) as max_bytes
from archiver.dropped_files
where ('${inputs.agency.value}' = '%' or coalesce(agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or feed_type = '${inputs.feed_type.value}')
group by all
order by files desc
```

<DataTable data={drop_labels} emptySet=pass emptyMessage="No dropped files 🎉"/>

```sql dropped_detail
select
    feed_type,
    date,
    system_name,
    name,
    size_bytes,
    response_code,
    content_length,
    label,
    -- same paste-into-shell guard as remediate; object names are
    -- archiver-generated so this never fires in practice
    case when name not like '%''%' then
        'uv run --script dashboards/proto-parquet-reconciliation/unpack.py --pb ''' || name || ''''
    end as unpack
from archiver.dropped_files
where ('${inputs.agency.value}' = '%' or coalesce(agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or feed_type = '${inputs.feed_type.value}')
    and label not in ('legitimately_empty_feed')
order by date, name
```

<Details title="Per-file detail (excluding legitimately-empty feeds)">
    <DataTable data={dropped_detail} rows=50 emptySet=pass emptyMessage="Nothing beyond legitimately-empty feeds"/>
</Details>

### Manual inspection

To eyeball raw contents against the compacted parquet for any file above, copy
its `unpack` command and run it from the repo root (requires ADC). It downloads
five consecutive snapshots centered on that file, parses each to JSON, pulls
the matching parquet rows (row-group-pruned, not the whole file), and writes
everything to `.scratch/inspect/` for side-by-side review:

```bash
uv run --script dashboards/proto-parquet-reconciliation/unpack.py \
    --pb 'trip_updates/date=2026-07-06/hour=.../base64url=.../<timestamp>.pb'
```

Output per window: raw `.pb` files, parsed `.json` (or `.PARSE_ERROR.txt` with
byte-level detail), `.meta.json` fetch sidecars, and `parquet_rows.csv` filtered
to those five source files — plus a terminal summary of entities-per-snapshot
vs parquet-rows-per-snapshot.

### Unmapped feeds

Feeds present in the raw bucket but absent from `feeds.parquet` — each one is a
finding in itself.

```sql unmapped
select base64url, url
from archiver.feeds
where agency_id = '(unmapped)'
order by base64url
```

<DataTable data={unmapped} emptySet=pass emptyMessage="All feeds in GCS are mapped in feeds.parquet 🎉"/>
