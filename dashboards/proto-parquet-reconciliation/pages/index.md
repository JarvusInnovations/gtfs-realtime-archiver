# Proto → Parquet Reconciliation

```sql extract_info
select
    start_date,
    end_date,
    coalesce(agency, 'all') as agency,
    coalesce(feed_type, 'all') as feed_type,
    extracted_at
from archiver.extract_meta
```

Extract window **<Value data={extract_info} column=start_date/> → <Value data={extract_info} column=end_date/>**
(agency: <Value data={extract_info} column=agency/>, feed type: <Value data={extract_info} column=feed_type/>),
extracted <Value data={extract_info} column=extracted_at/>.
Dates outside the buffered reconcilable window (older than ~358 days or newer
than 2 full days ago) are annotated, not trusted — see the plan for why.

```sql agencies
-- scoped to feeds present in this extract, so picking an agency the extract
-- never listed (e.g. under --agency) can't render as a false all-clear
select distinct f.agency_id, f.agency_name
from archiver.feeds f
where f.agency_id is not null
    and f.base64url in (select base64url from archiver.proto_files_hourly)
order by f.agency_id
```

```sql feed_types
select distinct feed_type from archiver.proto_files_hourly order by 1
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
feed-content changes.

```sql rows_per_proto
select
    date,
    coalesce(system_name, base64url) || ' · ' || feed_type as series,
    rows_per_proto
from archiver.daily_comparison
where ('${inputs.agency.value}' = '%' or coalesce(agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or feed_type = '${inputs.feed_type.value}')
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
own footer read failed, are labeled distinctly. The `remediate` column is the
paste-ready re-materialization command (`date|feed` multi-partition key):
compaction rewrites the whole partition from raw, so one run recovers anything
still within raw retention — but note it runs *local* code against
*production* buckets under your ADC, the feed dimension is dynamic (the key
must already be registered in Dagster), and it cannot recover `parse_failure`
files whose bytes were bad at fetch time.

```sql missing_partitions
select
    feed_type,
    date,
    agency_name,
    system_name,
    pb_count,
    pb_contentful_count,
    row_count,
    case
        when parquet_path is not null and row_count is null then 'footer read failed'
        when in_reconcilable_window then 'MISSING'
        else 'out of window'
    end as status,
    case
        when in_reconcilable_window and parquet_path is null and url is not null then
            'uv run dg launch --assets ' || feed_type || '_parquet --partition '''
            || strftime(date, '%Y-%m-%d') || '|'
            || case
                when starts_with(url, 'http://') then '~' || substr(url, 8)
                else regexp_replace(url, '^https://', '')
            end || ''''
    end as remediate
from archiver.daily_comparison
where ('${inputs.agency.value}' = '%' or coalesce(agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or feed_type = '${inputs.feed_type.value}')
    and pb_contentful_count > 0
    and coalesce(row_count, 0) = 0
order by in_reconcilable_window desc, date, feed_type
```

<DataTable data={missing_partitions} rows=25 emptySet=pass emptyMessage="No missing partitions in this window 🎉">
    <Column id=feed_type/>
    <Column id=date/>
    <Column id=agency_name/>
    <Column id=system_name/>
    <Column id=pb_count/>
    <Column id=pb_contentful_count/>
    <Column id=row_count/>
    <Column id=status/>
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
non-empty file, so the shortfall approximates dropped files (heuristic; the
labeled table below is the exact answer). Restricted to the reconcilable
window, matching the extract's escalation — an out-of-window shortfall would
have a by-construction-empty explanation below.

```sql short_partitions
select
    feed_type,
    date,
    agency_name,
    system_name,
    pb_contentful_count,
    num_row_groups,
    pb_contentful_count - num_row_groups as shortfall,
    row_count
from archiver.daily_comparison
where ('${inputs.agency.value}' = '%' or coalesce(agency_id, '(unmapped)') = '${inputs.agency.value}')
    and ('${inputs.feed_type.value}' = '%' or feed_type = '${inputs.feed_type.value}')
    and row_count > 0
    and num_row_groups < pb_contentful_count
    and in_reconcilable_window
order by shortfall desc
```

<DataTable data={short_partitions} rows=25 emptySet=pass emptyMessage="No short partitions 🎉"/>

### Dropped `.pb` files, classified

Exact per-file attribution for flagged partitions: files present in the raw
bucket whose path never appears in the parquet's `source_file` column, labeled
by parsing the file and reading its `.meta` sidecar.

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
    'uv run --script dashboards/proto-parquet-reconciliation/unpack.py --pb ''' || name || '''' as unpack
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
select base64url
from archiver.feeds
where agency_id = '(unmapped)'
order by base64url
```

<DataTable data={unmapped} emptySet=pass emptyMessage="All feeds in GCS are mapped in feeds.parquet 🎉"/>
