select
    d.feed_type,
    d.date,
    d.base64url,
    f.agency_id,
    f.agency_name,
    f.system_name,
    d.name,
    d.size_bytes,
    d.response_code,
    d.content_length,
    d.label
from dropped_files d
left join feeds f
    on f.base64url = d.base64url
    and (f.feed_type = d.feed_type or f.feed_type is null)
-- same contentful threshold as drop_summary, so the label/detail tables
-- correspond 1:1 with the shortfall arithmetic even against extracts that
-- classified header-only files (pre-2026-07-31)
where d.size_bytes > (select header_only_max from extract_meta)
order by d.date, d.name
