-- Pre-joined raw-vs-parquet comparison per feed x date over a complete date
-- spine: a feed-day with no raw files AND no parquet still produces a row,
-- so a total archiver outage renders as a gap instead of silently narrowing
-- the charts. The reconcilable window derives from extract_meta (the extract
-- emits its own window constants) so SQL and Python cannot drift.
with meta as (
    select
        cast(extracted_at as timestamp) as extracted_ts,
        cast(start_date as date) as start_date,
        cast(end_date as date) as end_date,
        window_old_days,
        window_new_days
    from extract_meta
),

proto_daily as (
    select
        feed_type,
        date,
        base64url,
        sum(pb_count) as pb_count,
        sum(meta_count) as meta_count,
        sum(zero_byte_count) as zero_byte_count,
        sum(header_only_count) as header_only_count
    from proto_files_hourly
    group by all
),

-- Feeds that appear anywhere in this extract (raw or parquet side). The
-- spine is scoped to these so an --agency run doesn't manufacture outage
-- rows for feeds it never listed.
active_feeds as (
    select distinct feed_type, base64url from proto_files_hourly
    union
    select distinct feed_type, base64url from parquet_daily
),

spine as (
    select a.feed_type, a.base64url, d.date
    from active_feeds a
    cross join (
        select unnest(generate_series(
            (select start_date from meta),
            (select end_date from meta),
            interval 1 day
        ))::date as date
    ) d
),

joined as (
    select
        s.feed_type,
        s.date,
        s.base64url,
        coalesce(p.pb_count, 0) as pb_count,
        coalesce(p.meta_count, 0) as meta_count,
        coalesce(p.zero_byte_count, 0) as zero_byte_count,
        coalesce(p.header_only_count, 0) as header_only_count,
        coalesce(p.pb_count, 0) - coalesce(p.zero_byte_count, 0)
            - coalesce(p.header_only_count, 0) as pb_contentful_count,
        q.row_count,
        q.size_bytes,
        q.num_row_groups,
        -- path present with NULL counts = footer read failed (extract-side
        -- sentinel), NOT a missing partition; views must distinguish them
        q.path as parquet_path
    from spine s
    left join proto_daily p
        on p.feed_type = s.feed_type and p.date = s.date and p.base64url = s.base64url
    left join parquet_daily q
        on q.feed_type = s.feed_type and q.date = s.date and q.base64url = s.base64url
)

select
    j.*,
    f.agency_id,
    f.agency_name,
    f.system_name,
    f.url,
    round(j.row_count / nullif(j.pb_contentful_count, 0), 1) as rows_per_proto,
    j.date between (select extracted_ts from meta)::date - (select window_old_days from meta)
        and (select extracted_ts from meta)::date - (select window_new_days from meta)
        as in_reconcilable_window
from joined j
left join feeds f
    on f.base64url = j.base64url
    and (f.feed_type = j.feed_type or f.feed_type is null)
