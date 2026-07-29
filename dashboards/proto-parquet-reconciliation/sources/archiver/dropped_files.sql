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
left join feeds f using (base64url)
order by d.date, d.name
