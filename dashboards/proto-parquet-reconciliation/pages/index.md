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
select distinct agency_id, agency_name
from archiver.feeds
where agency_id is not null
order by agency_id
```

<Dropdown data={agencies} name=agency value=agency_id label=agency_name title="Agency">
    <DropdownOption value="%" valueLabel="All agencies"/>
</Dropdown>

<Dropdown name=feed_type title="Feed type">
    <DropdownOption value="%" valueLabel="All feed types"/>
    <DropdownOption value="vehicle_positions"/>
    <DropdownOption value="trip_updates"/>
    <DropdownOption value="service_alerts"/>
</Dropdown>

## Hourly archiver coverage

Raw `.pb` files archived per hour (UTC). Gaps jump out as missing cells.

```sql hourly
select
    strftime(date, '%Y-%m-%d') as date,
    hour,
    sum(pb_count) as pb_count
from archiver.proto_files_hourly
where coalesce(agency_id, '(unmapped)') like '${inputs.agency.value}'
    and feed_type like '${inputs.feed_type.value}'
group by all
order by date, hour
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
where coalesce(agency_id, '(unmapped)') like '${inputs.agency.value}'
    and feed_type like '${inputs.feed_type.value}'
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
    system_name || ' · ' || feed_type as series,
    rows_per_proto
from archiver.daily_comparison
where coalesce(agency_id, '(unmapped)') like '${inputs.agency.value}'
    and feed_type like '${inputs.feed_type.value}'
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

Raw `.pb` files exist but the daily parquet is missing or empty. Rows outside
the reconcilable window are expected noise (compaction hasn't run yet, or raw
data has been reaped) and are labeled.

```sql missing_partitions
select
    feed_type,
    date,
    agency_name,
    system_name,
    pb_count,
    row_count,
    case when in_reconcilable_window then 'MISSING' else 'out of window' end as status
from archiver.daily_comparison
where coalesce(agency_id, '(unmapped)') like '${inputs.agency.value}'
    and feed_type like '${inputs.feed_type.value}'
    and pb_count > 0
    and coalesce(row_count, 0) = 0
order by in_reconcilable_window desc, date, feed_type
```

<DataTable data={missing_partitions} rows=25 emptySet=pass emptyMessage="No missing partitions in this window 🎉">
    <Column id=status contentType=colorscale colorScale={['#dc2626','#d2c6ac']}/>
</DataTable>

### Probable drops during compaction

Partitions where row groups fall short of non-empty `.pb` files — one row group
is written per successfully-parsed non-empty file, so the shortfall approximates
dropped files (heuristic; the labeled table below is the exact answer).

```sql short_partitions
select
    feed_type,
    date,
    agency_name,
    system_name,
    pb_nonzero_count,
    num_row_groups,
    pb_nonzero_count - num_row_groups as shortfall,
    row_count
from archiver.daily_comparison
where coalesce(agency_id, '(unmapped)') like '${inputs.agency.value}'
    and feed_type like '${inputs.feed_type.value}'
    and row_count > 0
    and num_row_groups < pb_nonzero_count
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
where coalesce(agency_id, '(unmapped)') like '${inputs.agency.value}'
    and feed_type like '${inputs.feed_type.value}'
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
    label
from archiver.dropped_files
where coalesce(agency_id, '(unmapped)') like '${inputs.agency.value}'
    and feed_type like '${inputs.feed_type.value}'
    and label not in ('legitimately_empty_feed')
order by date, name
```

<Details title="Per-file detail (excluding legitimately-empty feeds)">
    <DataTable data={dropped_detail} rows=50 emptySet=pass emptyMessage="Nothing beyond legitimately-empty feeds"/>
</Details>

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
