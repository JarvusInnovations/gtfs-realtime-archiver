-- Pre-joined raw-vs-parquet comparison per feed x date, with the buffered
-- reconcilable-window flag (old edge: ~358 days, async lifecycle reaping can
-- partially reap boundary days; new edge: 2 full days, compaction *starts* at
-- 02:00 UTC for the previous day and can run for hours).
with proto_daily as (
    select
        feed_type,
        date,
        base64url,
        sum(pb_count) as pb_count,
        sum(meta_count) as meta_count,
        sum(zero_byte_count) as zero_byte_count
    from proto_files_hourly
    group by all
),

meta as (
    select cast(extracted_at as timestamp) as extracted_ts from extract_meta
)

select
    feed_type,
    date,
    base64url,
    f.agency_id,
    f.agency_name,
    f.system_name,
    coalesce(p.pb_count, 0) as pb_count,
    coalesce(p.meta_count, 0) as meta_count,
    coalesce(p.zero_byte_count, 0) as zero_byte_count,
    coalesce(p.pb_count, 0) - coalesce(p.zero_byte_count, 0) as pb_nonzero_count,
    q.row_count,
    q.size_bytes,
    q.num_row_groups,
    round(
        q.row_count / nullif(coalesce(p.pb_count, 0) - coalesce(p.zero_byte_count, 0), 0),
        1
    ) as rows_per_proto,
    date between (select extracted_ts from meta)::date - 358
        and (select extracted_ts from meta)::date - 2
        as in_reconcilable_window
from proto_daily p
full outer join parquet_daily q using (feed_type, date, base64url)
left join feeds f on f.base64url = coalesce(p.base64url, q.base64url)
