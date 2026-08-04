select
    base64url,
    url,
    feed_type,
    agency_id,
    agency_name,
    system_id,
    system_name
from feeds
-- Belt-and-braces only: the real dedupe lives at ingest (FEEDS_INGEST_SQL in
-- extract.py), because Evidence sources run standalone and the page joins hit
-- the raw feeds table, not this source. This copy covers databases created by
-- pre-2026-08-04 extracts.
qualify row_number() over (
    partition by base64url, feed_type
    order by agency_id, system_id
) = 1
order by agency_name, system_name, feed_type
