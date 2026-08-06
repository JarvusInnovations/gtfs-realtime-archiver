-- Per-partition rollup of classified drops, shared by the missing-partitions
-- diagnosis and the short-partitions explanation columns so the two can't
-- drift (they previously carried duplicate CTEs with divergent aliases).
-- The size filter derives the contentful threshold from the extract itself
-- and keeps this correct even against pre-2026-07-31 extracts that
-- classified header-only files.
select
    feed_type,
    date,
    base64url,
    count(*) as classified,
    count(*) filter (label = 'unexplained_drop') as valid_dropped,
    count(*) filter (label = 'parse_failure') as parse_failure,
    count(*) filter (label = 'legitimately_empty_feed') as zero_entity,
    count(*) filter (label = 'error') as errored
from dropped_files
where size_bytes > (select header_only_max from extract_meta)
group by all
