select
    base64url,
    url,
    feed_type,
    agency_id,
    agency_name,
    system_id,
    system_name
from feeds
-- feeds.parquet carries no uniqueness guarantee: a (url, feed_type) listed
-- twice in agencies.yaml would fan out every join on the page and silently
-- double the charts. Dedupe defensively; the extract warns when it happens.
qualify row_number() over (
    partition by base64url, feed_type
    order by agency_id, system_id
) = 1
order by agency_name, system_name, feed_type
