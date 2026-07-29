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
    p.zero_byte_count
from proto_files_hourly p
left join feeds f using (base64url)
