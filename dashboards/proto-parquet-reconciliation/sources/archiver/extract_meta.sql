select
    start_date,
    end_date,
    -- explicit casts: an all-NULL column would otherwise be typed DOUBLE in
    -- the materialized parquet, breaking string coalesce in page queries
    cast(agency as varchar) as agency,
    cast(feed_type as varchar) as feed_type,
    extracted_at,
    window_old_days,
    window_new_days,
    header_only_max,
    window_anchor_date
from extract_meta
