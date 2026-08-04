select
    p.feed_type,
    p.date,
    p.hour,
    p.base64url,
    f.agency_id,
    f.agency_name,
    f.system_name,
    p.pb_count,
    p.meta_count,
    p.zero_byte_count,
    p.header_only_count
from proto_files_hourly p
-- feeds.parquet is one row per (url, feed_type); joining on base64url alone
-- would fan out if a URL were ever reused across feed types. Unmapped feeds
-- carry NULL feed_type, hence the OR branch.
left join feeds f
    on f.base64url = p.base64url
    and (f.feed_type = p.feed_type or f.feed_type is null)
