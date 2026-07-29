select
    base64url,
    url,
    feed_type,
    agency_id,
    agency_name,
    system_id,
    system_name
from feeds
order by agency_name, system_name, feed_type
