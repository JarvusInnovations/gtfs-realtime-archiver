-- Pre-joined raw-vs-parquet comparison per feed x date, with the buffered
-- reconcilable-window flag (old edge: ~358 days, async lifecycle reaping can
-- partially reap boundary days; new edge: 2 full days, compaction *starts* at
-- 02:00 UTC and can run for hours).
with proto_daily as (
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

meta as (
    select cast(extracted_at as timestamp) as extracted_ts from extract_meta
),

joined as (
    select
        -- explicit coalesced keys: don't lean on USING-binding precedence
        coalesce(p.feed_type, q.feed_type) as feed_type,
        coalesce(p.date, q.date) as date,
        coalesce(p.base64url, q.base64url) as base64url,
        coalesce(p.pb_count, 0) as pb_count,
        coalesce(p.meta_count, 0) as meta_count,
        coalesce(p.zero_byte_count, 0) as zero_byte_count,
        coalesce(p.header_only_count, 0) as header_only_count,
        coalesce(p.pb_count, 0) - coalesce(p.zero_byte_count, 0)
            - coalesce(p.header_only_count, 0) as pb_contentful_count,
        q.row_count,
        q.size_bytes,
        q.num_row_groups
    from proto_daily p
    full outer join parquet_daily q
        on p.feed_type = q.feed_type
        and p.date = q.date
        and p.base64url = q.base64url
)

select
    j.*,
    f.agency_id,
    f.agency_name,
    f.system_name,
    round(j.row_count / nullif(j.pb_contentful_count, 0), 1) as rows_per_proto,
    j.date between (select extracted_ts from meta)::date - 358
        and (select extracted_ts from meta)::date - 2
        as in_reconcilable_window
from joined j
left join feeds f
    on f.base64url = j.base64url
    and (f.feed_type = j.feed_type or f.feed_type is null)
