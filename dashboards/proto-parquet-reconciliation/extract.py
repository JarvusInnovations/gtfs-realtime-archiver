#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "google-cloud-storage>=2.14",
#     "gcsfs>=2024.2.0",
#     "pyarrow>=22",
#     "duckdb>=1.0",
#     "gtfs-realtime-bindings>=2.2.0",
# ]
# ///
"""Extract reconciliation data: GCS -> data/reconciliation.duckdb.

Compares raw .pb archive counts against compacted parquet row counts so the
Evidence dashboard in this directory can surface missing partitions and
dropped files. See plans/proto-parquet-reconciliation-dashboard.md.

Temporary tooling: deliberately outside the repo's mypy/ruff CI paths.

Requires ADC with read access to the raw protobuf bucket:
    gcloud auth application-default login
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

import pyarrow as pa
import pyarrow.parquet as pq
from google.api_core.exceptions import Forbidden, NotFound
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

# duckdb is imported inside main(): it's the one dependency absent from the
# repo's dev groups, and deferring it keeps this module importable from
# tests/ for behavioral tests of the pure helpers.

PROTOBUF_BUCKET = os.environ.get("GCS_BUCKET_RT_PROTOBUF", "protobuf.gtfsrt.io")
PARQUET_BUCKET = os.environ.get("GCS_BUCKET_RT_PARQUET", "parquet.gtfsrt.io")
FEEDS_PARQUET_URL = f"https://storage.googleapis.com/{PARQUET_BUCKET}/feeds.parquet"
FEED_TYPES = ["vehicle_positions", "trip_updates", "service_alerts"]
LIST_WORKERS = 32
DB_PATH = Path(__file__).parent / "data" / "reconciliation.duckdb"

# GTFS-RT entity field per feed_type, for classifying anti-joined drops the
# same way the compaction extractors would see them.
ENTITY_FIELDS = {
    "vehicle_positions": "vehicle",
    "trip_updates": "trip_update",
    "service_alerts": "alert",
}

# A .pb at or below this size is almost certainly a header-only FeedMessage
# (observed empty SEPTA feeds are 15 bytes; the smallest message carrying one
# entity is ~21 bytes, so the margin is thin — a full header is 15 bytes and a
# minimal entity adds 6). Used only for the cheap aggregate counts
# (header_only_count, contentful_counts); classification always parses the
# actual bytes, so a truncated sub-20-byte write still surfaces as
# parse_failure rather than being assumed empty.
HEADER_ONLY_MAX = 20

# Ported from inventory.py's _RT_PATTERN so the two definitions of a valid RT
# parquet path can't drift (this script can't import from src/).
_RT_PATTERN = re.compile(
    r"^(?P<feed_type>[^/]+)/date=(?P<date>\d{4}-\d{2}-\d{2})"
    r"/base64url=(?P<base64url>[A-Za-z0-9_-]+)/data\.parquet$"
)

# The reconcilable window, buffered at both edges (see the plan): older than
# ~358 days raw objects may be partially reaped; newer than 2 full days
# compaction may not have finished.
WINDOW_OLD_DAYS = 358
WINDOW_NEW_DAYS = 2

# Dedupe feeds AT INGEST: Evidence source queries run standalone against the
# raw tables (they cannot reference each other), so a dedupe in feeds.sql
# would not protect the joins in daily_comparison/proto_files_hourly/
# dropped_files — a duplicated (url, feed_type) in agencies.yaml would fan
# them out and silently double the charts. Every consumer inherits this.
FEEDS_INGEST_SQL = """CREATE TABLE feeds AS
    SELECT * FROM feeds_arrow
    QUALIFY row_number() OVER (
        PARTITION BY base64url, feed_type
        ORDER BY agency_id, system_id
    ) = 1"""

# Shared with tests: the fixture DB must derive proto_files_hourly exactly the
# way the extract does, or the SQL tests validate a stale shape.
PROTO_FILES_HOURLY_DDL = """CREATE TABLE proto_files_hourly AS
    SELECT feed_type, date, hour, base64url,
           count(r.name) AS pb_count,
           coalesce(any_value(m.meta_count), 0) AS meta_count,
           count(r.name) FILTER (r.size_bytes = 0) AS zero_byte_count,
           count(r.name) FILTER (r.size_bytes > 0
                                 AND r.size_bytes <= {header_only_max})
               AS header_only_count
    FROM raw_files r
    -- FULL OUTER: an hour with .meta sidecars but no .pb (or vice versa)
    -- must still produce a row
    FULL OUTER JOIN meta_counts m USING (feed_type, date, hour, base64url)
    GROUP BY ALL"""

# Missing partitions (no parquet at all) normally skip per-file attribution —
# nothing was written, so "which files dropped" is everything, and classifying
# a busy feed's whole day is a download storm. But when the day held only a
# handful of contentful files, classifying them explains WHY nothing was
# written: e.g. a partition whose sole contentful .pb is the fleet-wide
# "ERROR: no connectivity to BusTime server!" HTTP-200 body is
# missing-and-unrecoverable, not missing-and-remediable.
MISSING_CLASSIFY_MAX = 25


def _stride_cap(items: list, cap: int, label: str, flag: str) -> list:
    """Cap a candidate list by even stride across the whole list — a
    head-of-list truncation would sample only the earliest timestamps of the
    day. cap <= 0 means "classify nothing" (compare-only runs) rather than
    ZeroDivisionError after all the listing work.
    """
    if cap <= 0:
        # Silent per call: the run-level "skipped in total" summary reports it
        # once instead of once per flagged partition.
        return []
    if len(items) <= cap:
        return items
    print(f"  NOTE: {label}: classifying {cap} of {len(items)} ({flag} cap)")
    stride = len(items) / cap
    return [items[int(i * stride)] for i in range(cap)]


def with_retries(fn, attempts: int = 3):
    """Retry a worker function on transient errors with linear backoff.

    Terminal API errors (404/403) re-raise immediately — retrying a
    guaranteed failure burns ~3s of backoff per call, which adds up across
    thousands of classify candidates.
    """

    def wrapped(*a, **k):
        for i in range(attempts):
            try:
                return fn(*a, **k)
            # gcsfs raises the stdlib equivalents for the same conditions
            except (NotFound, Forbidden, FileNotFoundError, PermissionError):
                raise
            except Exception:
                if i == attempts - 1:
                    raise
                time.sleep(1 + 2 * i)

    return wrapped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agency",
        help="Agency id slug (feeds.parquet agency_id) to scope extraction cost. "
        "Display filtering is the dashboard dropdown's job.",
    )
    parser.add_argument(
        "--feed-type",
        choices=FEED_TYPES,
        help="Limit to one feed type (prunes whole top-level prefixes)",
    )
    parser.add_argument("--start", help="First date, YYYY-MM-DD (default: 14 days ago)")
    parser.add_argument("--end", help="Last date, YYYY-MM-DD (default: yesterday, UTC)")
    parser.add_argument(
        "--output",
        type=Path,
        default=DB_PATH,
        help=f"Output DuckDB path (default: {DB_PATH}). Each run overwrites it.",
    )
    parser.add_argument(
        "--max-classify",
        type=int,
        default=1000,
        help="Per-partition cap on dropped-file classification downloads; "
        "truncation is logged; 0 disables classification (default 1000)",
    )
    parser.add_argument(
        "--max-classify-total",
        type=int,
        default=20000,
        help="Run-level cap on classification downloads across all flagged "
        "partitions (~2 GETs each); even-stride sampled, truncation logged; "
        "0 disables classification (default 20000)",
    )
    return parser.parse_args()


def date_range(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def fetch_feeds() -> pa.Table:
    """Feed dimension from the public feeds.parquet."""
    with urlopen(FEEDS_PARQUET_URL, timeout=30) as resp:
        data = resp.read()
    return pq.read_table(
        pa.BufferReader(data),
        columns=[
            "base64url",
            "url",
            "feed_type",
            "agency_id",
            "agency_name",
            "system_id",
            "system_name",
        ],
    )


def hour_prefixes(feed_types: list[str], dates: list[str]) -> list[tuple[str, str, int, str]]:
    """(feed_type, date, hour, prefix) for every listable hour partition."""
    out = []
    for ft in feed_types:
        for d in dates:
            for h in range(24):
                prefix = f"{ft}/date={d}/hour={d}T{h:02d}:00:00Z/"
                out.append((ft, d, h, prefix))
    return out


def discover_feeds_in_hour(client, task):
    """Delimiter listing: just the base64url= sub-prefixes present in this hour.

    Keeps unmapped-feed detection working even when --agency scopes the object
    listing to known feeds: this pass sees every feed actually in GCS.
    """
    ft, d, h, prefix = task
    it = client.list_blobs(PROTOBUF_BUCKET, prefix=prefix, delimiter="/")
    # Prefixes are only populated after consuming the iterator pages.
    for _page in it.pages:
        pass
    found = []
    for p in it.prefixes:
        # {prefix}base64url={b64}/
        seg = p[len(prefix) :].strip("/")
        if seg.startswith("base64url="):
            found.append((ft, d, h, seg[len("base64url=") :]))
    return found


# Intern parsed base64url values: in full-range mode every object would
# otherwise allocate its own ~120-char copy of one of ~71 distinct strings —
# noticeable (~1GB) across millions of listed objects. dict.setdefault is
# GIL-atomic, so sharing across listing threads is safe.
_B64_CACHE: dict[str, str] = {}


def list_raw_objects(client, task):
    """List one prefix → (.pb rows, .meta counts).

    Only .pb objects keep their names (the anti-join and size classification
    need them); .meta sidecars are reduced to a per-partition counter on the
    spot, which roughly halves peak memory on fleet-wide runs.
    """
    ft, d, h, prefix, b64 = task
    rows = []
    meta_counts: dict[tuple, int] = {}
    for blob in client.list_blobs(PROTOBUF_BUCKET, prefix=prefix):
        name = blob.name
        b64_val = b64
        if b64_val is None:
            # Parse base64url from the path: .../base64url={b64}/{ts}.{ext}
            try:
                parsed = name.split("base64url=")[1].split("/")[0]
            except IndexError:
                continue
            b64_val = _B64_CACHE.setdefault(parsed, parsed)
        if name.endswith(".meta"):
            key = (ft, d, h, b64_val)
            meta_counts[key] = meta_counts.get(key, 0) + 1
        elif name.endswith(".pb"):
            rows.append((ft, d, h, b64_val, name, blob.size or 0))
    return rows, meta_counts


def list_parquet_partitions(client, task):
    """List one {feed_type}/date={d}/ prefix of the parquet bucket."""
    ft, d = task
    rows = []
    prefix = f"{ft}/date={d}/"
    for blob in client.list_blobs(PARQUET_BUCKET, prefix=prefix):
        match = _RT_PATTERN.match(blob.name)
        if not match:
            continue
        rows.append((ft, d, match.group("base64url"), blob.name, blob.size or 0))
    return rows


def read_footer(fs, path: str):
    """(row_count, num_row_groups) from a parquet footer via range reads."""
    md = pq.read_metadata(f"{PARQUET_BUCKET}/{path}", filesystem=fs)
    return int(md.num_rows), int(md.num_row_groups)


def read_source_files(full_path: str, fs=None) -> set[str]:
    """Distinct source_file values from one parquet (single-column read).

    Takes a full path (and optional filesystem) so tests can exercise it
    against a local file. unique() runs Arrow-side: the column arrives
    dictionary-encoded, so this avoids materializing tens of millions of
    Python strings for big partitions.
    """
    import pyarrow.compute as pc

    # read_dictionary keeps the column dictionary-encoded (~150MB instead of
    # ~5GB decoded for a 36M-row partition) — essential with 8 reads in flight.
    table = pq.read_table(
        full_path,
        columns=["source_file"],
        filesystem=fs,
        read_dictionary=["source_file"],
    )
    # Dedupe per chunk: never concatenates, so the 2GB int32-offset limit on
    # `string` arrays can't overflow no matter how large the column is. The
    # per-chunk unique output is tiny (one value per contributing .pb file).
    sources: set[str] = set()
    for chunk in table.column("source_file").chunks:
        if pa.types.is_dictionary(chunk.type):
            # Null indices would yield a None in the set — harmless for the
            # `name not in sources` anti-join, and unreachable anyway while
            # the schema declares source_file non-nullable.
            vals = chunk.dictionary.take(pc.unique(chunk.indices))
        else:
            vals = pc.unique(chunk)
        sources.update(vals.to_pylist())
    return sources


def classify_drop(bucket, feed_type: str, pb_name: str):
    """Label one anti-joined .pb: why did it not contribute rows?

    Reads the sibling .meta (response_code/content_length) and, since the
    anti-joined set is tiny, downloads and parses the .pb itself for the exact
    parse-failure vs zero-entities distinction the compaction loop makes.
    """
    # Candidates are pre-filtered to contentful sizes (> HEADER_ONLY_MAX), so
    # zero-byte and header-only files never reach here; their story is told by
    # the aggregate counts. Small-but-contentful files are parsed, never
    # assumed empty — truncated tiny writes are exactly the failure class this
    # tool exists to catch.
    meta_name = pb_name.rsplit(".", 1)[0] + ".meta"
    response_code = None
    content_length = None
    try:
        meta = json.loads(bucket.blob(meta_name).download_as_text())
        response_code = meta.get("response_code")
        content_length = meta.get("content_length")
    except Exception:
        pass  # missing sidecar or transient error: fields stay NULL

    content = bucket.blob(pb_name).download_as_bytes()
    try:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(content)
        entity_field = ENTITY_FIELDS[feed_type]
        n_entities = sum(1 for e in feed.entity if e.HasField(entity_field))
        label = "legitimately_empty_feed" if n_entities == 0 else "unexplained_drop"
    except (DecodeError, ValueError):
        # Matches compaction.py's own except: what the pipeline calls a parse
        # failure, we call a parse failure. No bare except here — transient
        # download errors must propagate so with_retries can retry them;
        # safe_classify labels terminal failures "error".
        label = "parse_failure"

    return response_code, content_length, label


def main() -> int:
    args = parse_args()

    today_utc = datetime.now(timezone.utc).date()  # partitions are UTC-keyed
    end = date.fromisoformat(args.end) if args.end else today_utc - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=13)
    if start > end:
        print(f"--start {start} is after --end {end}", file=sys.stderr)
        return 1
    dates = date_range(start, end)
    feed_types = [args.feed_type] if args.feed_type else FEED_TYPES

    import duckdb  # deferred: see module-scope note
    from google.cloud import storage  # deferred: slow import

    client = storage.Client()
    # The shared requests session defaults to a 10-connection pool; below
    # LIST_WORKERS threads that means connection churn, not throughput.
    from requests.adapters import HTTPAdapter

    adapter = HTTPAdapter(pool_connections=LIST_WORKERS, pool_maxsize=LIST_WORKERS)
    # Private API; guard so a google-cloud-storage reshape degrades to
    # "slower" (default 10-connection pool) instead of AttributeError.
    if hasattr(client, "_http") and hasattr(client._http, "mount"):
        client._http.mount("https://", adapter)
    import gcsfs

    fs = gcsfs.GCSFileSystem()

    # 1. Feed dimension ----------------------------------------------------
    print(f"Fetching {FEEDS_PARQUET_URL}")
    try:
        feeds = with_retries(fetch_feeds)()
    except Exception as e:
        print(
            f"Could not fetch {FEEDS_PARQUET_URL} ({e}) — this script reads "
            "feeds.parquet anonymously over HTTPS from the public parquet bucket",
            file=sys.stderr,
        )
        return 1
    feeds_rows = feeds.to_pylist()
    feeds_by_b64 = {r["base64url"]: r for r in feeds_rows}
    print(f"  {feeds.num_rows} feeds in feeds.parquet")
    # feeds.parquet has no uniqueness guarantee; a duplicated (url, feed_type)
    # would — absent the ingest dedupe in FEEDS_INGEST_SQL — fan out every
    # feeds join on the dashboard. Surface it as a finding regardless.
    seen_pairs: set[tuple] = set()
    for r in feeds_rows:
        pair = (r["base64url"], r["feed_type"])
        if pair in seen_pairs:
            print(
                f"  WARNING: duplicate feeds.parquet entry for {r['url']} "
                f"({r['feed_type']}) — dashboard joins are deduplicated, but "
                "fix agencies.yaml",
                file=sys.stderr,
            )
        seen_pairs.add(pair)

    agency_b64s: set[str] | None = None
    if args.agency:
        agency_b64s = {r["base64url"] for r in feeds_rows if r["agency_id"] == args.agency}
        if not agency_b64s:
            known = sorted({r["agency_id"] for r in feeds_rows})
            print(
                f"No feeds for agency '{args.agency}'. Known: {', '.join(known)}", file=sys.stderr
            )
            return 1
        print(f"  --agency {args.agency}: {len(agency_b64s)} feeds")

    # 2. Raw side ----------------------------------------------------------
    # Discovery pass only in agency mode: a full-range object listing walks
    # the same hour prefixes anyway, so the delimiter pass would be ~1000
    # redundant calls per 14-day run. In agency mode it's what keeps
    # unmapped-feed detection alive (the exact-prefix fan-out is otherwise
    # blind to feeds not derived from feeds.parquet).
    hours = hour_prefixes(feed_types, dates)
    list_raw = with_retries(lambda t: list_raw_objects(client, t))
    if agency_b64s is not None:
        print(f"Discovery pass: {len(hours)} hour prefixes (delimiter listing)")
        discovered: list[tuple[str, str, int, str]] = []  # (ft, date, hour, b64)
        discover = with_retries(lambda t: discover_feeds_in_hour(client, t))
        with ThreadPoolExecutor(LIST_WORKERS) as pool:
            for found in pool.map(discover, hours):
                discovered.extend(found)
        discovered_set = set(discovered)
        list_tasks = [
            (ft, d, h, f"{prefix}base64url={b64}/", b64)
            for (ft, d, h, prefix) in hours
            for b64 in sorted(agency_b64s)
            if (ft, d, h, b64) in discovered_set
        ]
    else:
        list_tasks = [(ft, d, h, prefix, None) for (ft, d, h, prefix) in hours]
    print(f"Object listing: {len(list_tasks)} prefixes")
    raw_files: list[tuple] = []
    meta_counts: dict[tuple, int] = {}
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        for rows, metas in pool.map(list_raw, list_tasks):
            raw_files.extend(rows)
            for key, n in metas.items():
                meta_counts[key] = meta_counts.get(key, 0) + n
    print(f"  {len(raw_files)} .pb objects listed ({sum(meta_counts.values())} .meta counted)")

    if agency_b64s is None:
        # Full-range mode: presence falls out of the object listing directly.
        # Union .meta-only partitions so a sidecar-only feed doesn't escape
        # unmapped-feed detection in this mode (agency mode's delimiter pass
        # sees every base64url prefix regardless of contents).
        discovered = sorted(
            {(ft, d, h, b64) for (ft, d, h, b64, _n, _s) in raw_files} | set(meta_counts.keys())
        )
    discovered_b64s = {b64 for _, _, _, b64 in discovered}
    print(f"  {len(discovered_b64s)} distinct feeds present in raw bucket")

    # 3. Parquet side (windowed by construction) ---------------------------
    pq_tasks = [(ft, d) for ft in feed_types for d in dates]
    print(f"Parquet listing: {len(pq_tasks)} date prefixes")
    list_pq = with_retries(lambda t: list_parquet_partitions(client, t))
    pq_objects: list[tuple] = []
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        for rows in pool.map(list_pq, pq_tasks):
            pq_objects.extend(rows)

    # Footer reads: scope to selected feeds in agency mode. A footer that
    # still fails after retries becomes a sentinel row (path kept, counts
    # NULL) rather than killing the run.
    def safe_footer(path: str):
        try:
            return with_retries(read_footer)(fs, path)
        except Exception as e:
            print(f"  WARNING: footer read failed for {path}: {e}", file=sys.stderr)
            return None, None

    selected = agency_b64s
    footer_targets = [
        (ft, d, b64, path, size)
        for (ft, d, b64, path, size) in pq_objects
        if selected is None or b64 in selected
    ]
    print(f"Footer reads: {len(footer_targets)} partitions")
    parquet_daily: list[tuple] = []
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        footers = pool.map(lambda t: safe_footer(t[3]), footer_targets)
        for (ft, d, b64, path, size), (rows, groups) in zip(footer_targets, footers):
            parquet_daily.append((ft, d, b64, rows, size, groups, path))

    # Null rows for partitions expected from the raw side but absent.
    have = {(ft, d, b64) for (ft, d, b64, *_rest) in parquet_daily}
    raw_partitions = {(ft, d, b64) for (ft, d, h, b64) in discovered}
    for ft, d, b64 in sorted(raw_partitions - have):
        if selected is not None and b64 not in selected:
            continue
        parquet_daily.append((ft, d, b64, None, None, None, None))

    # 4. Discrepancy escalation --------------------------------------------
    # Per-partition "contentful" pb count (bigger than a header-only message,
    # so capable of producing a row group).
    contentful_counts: dict[tuple, int] = {}
    for ft, d, h, b64, name, size in raw_files:  # raw_files holds .pb only
        if size > HEADER_ONLY_MAX:
            key = (ft, d, b64)
            contentful_counts[key] = contentful_counts.get(key, 0) + 1

    # Escalation only makes sense inside the reconcilable window (outside it,
    # discrepancies are expected: compaction hasn't run / raw was reaped), and
    # only for partitions where a parquet exists — when nothing was written,
    # the missing-partition row IS the signal, and per-file attribution would
    # just re-download the whole partition to prove it.
    window_lo = (today_utc - timedelta(days=WINDOW_OLD_DAYS)).isoformat()
    window_hi = (today_utc - timedelta(days=WINDOW_NEW_DAYS)).isoformat()
    flagged = []
    for ft, d, b64, rows, size, groups, path in parquet_daily:
        if not (window_lo <= d <= window_hi):
            continue
        contentful_n = contentful_counts.get((ft, d, b64), 0)
        # Bound on the row-group identity: pyarrow splits a write_table call
        # above ~1Mi rows, so a single .pb yielding >1,048,576 rows would add
        # row groups and mask shortfall (fails safe: hides, never invents).
        # Largest observed snapshot is ~18k rows — ~50x headroom.
        if path is None:
            # Missing partition: classify only the low-volume case (see
            # MISSING_CLASSIFY_MAX) so the drops table explains it.
            if 0 < contentful_n <= MISSING_CLASSIFY_MAX:
                flagged.append((ft, d, b64, None))
            continue
        # rows == 0 is belt-and-braces: compaction never uploads a 0-row
        # parquet (writer stays None), so only the shortfall branch fires in
        # practice.
        if rows == 0 or (rows is not None and groups is not None and groups < contentful_n):
            flagged.append((ft, d, b64, path))
    eff_lo = max(window_lo, start.isoformat())
    eff_hi = min(window_hi, end.isoformat())
    window_desc = f"{eff_lo}..{eff_hi}" if eff_lo <= eff_hi else "none"
    print(
        f"Escalation: {len(flagged)} flagged partitions "
        f"(reconcilable dates in this run: {window_desc})"
    )

    dropped: list[tuple] = []
    protobuf_bucket = client.bucket(PROTOBUF_BUCKET)
    flagged_keys = {(ft, d, b64) for (ft, d, b64, _p) in flagged}
    raw_pb_by_partition: dict[tuple, list[tuple[str, int]]] = {}
    for ft, d, h, b64, name, size in raw_files:  # raw_files holds .pb only
        if (ft, d, b64) in flagged_keys:
            raw_pb_by_partition.setdefault((ft, d, b64), []).append((name, size))

    # Parallel source_file column reads, then parallel per-file
    # classification. A column read that fails after retries skips that
    # partition's attribution (with a warning) instead of killing the run.
    print(f"  reading source_file column for {len(flagged)} partitions")
    source_sets: dict[tuple, set[str]] = {}

    def safe_sources(path: str):
        try:
            return with_retries(read_source_files)(f"{PARQUET_BUCKET}/{path}", fs=fs)
        except Exception as e:
            print(f"  WARNING: source_file read failed for {path}: {e}", file=sys.stderr)
            return None

    # Capped below LIST_WORKERS: each read holds a whole column in memory.
    with_parquet = [t for t in flagged if t[3] is not None]
    with ThreadPoolExecutor(8) as pool:
        for (ft, d, b64, path), sources in zip(
            with_parquet, pool.map(lambda t: safe_sources(t[3]), with_parquet)
        ):
            if sources is None:
                continue
            source_sets[(ft, d, b64)] = sources
    # Missing partitions have no parquet: every file is by definition
    # uncontributed, so the anti-join runs against an empty source set.
    for ft, d, b64, path in flagged:
        if path is None:
            source_sets[(ft, d, b64)] = set()

    candidates: list[tuple] = []
    skipped_classify = 0
    for ft, d, b64, path in flagged:
        sources = source_sets.get((ft, d, b64))
        if sources is None:  # column read failed: no attribution possible
            continue
        # Only contentful files need explaining, for missing and shortfall
        # partitions alike: zero-byte/header-only files never produce a row
        # group, don't count toward the shortfall metric, and are already
        # counted in the aggregates — classifying them (~14k downloads per
        # fleet run) told us nothing their size didn't. This also makes
        # dropped_files correspond 1:1 with the shortfall arithmetic.
        part_files = [
            (n, s)
            for n, s in sorted(raw_pb_by_partition.get((ft, d, b64), []))
            if s > HEADER_ONLY_MAX
        ]
        part = [(ft, d, b64, name, size) for name, size in part_files if name not in sources]
        capped = _stride_cap(part, args.max_classify, f"{ft}/{d}/{b64[:16]}…", "--max-classify")
        skipped_classify += len(part) - len(capped)
        candidates.extend(capped)
    n_before = len(candidates)
    candidates = _stride_cap(
        candidates, args.max_classify_total, "run total", "--max-classify-total"
    )
    skipped_classify += n_before - len(candidates)
    if skipped_classify:
        print(f"  NOTE: {skipped_classify} candidates skipped in total (caps)")
    print(f"  classifying {len(candidates)} dropped .pb files")

    def safe_classify(c):
        try:
            return with_retries(classify_drop)(protobuf_bucket, c[0], c[3])
        except Exception:
            return None, None, "error"

    done = 0
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        results = pool.map(safe_classify, candidates)
        for (ft, d, b64, name, size), (code, clen, label) in zip(candidates, results):
            dropped.append((ft, d, b64, name, size, code, clen, label))
            done += 1
            if done % 1000 == 0:
                print(f"    {done}/{len(candidates)}")
    print(f"  {len(dropped)} dropped .pb files classified")

    # 5. Write DuckDB -------------------------------------------------------
    out_path: Path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    # An orphaned WAL from an interrupted run would otherwise be replayed
    # into the fresh database.
    out_path.with_name(out_path.name + ".wal").unlink(missing_ok=True)
    con = duckdb.connect(str(out_path))

    con.register("feeds_arrow", feeds)
    con.execute(FEEDS_INGEST_SQL)
    # Unmapped feeds: present in GCS, absent from feeds.parquet. Named columns
    # so this can't silently shift if fetch_feeds()'s column list changes.
    # The decoded URL makes the finding actionable (same decode as
    # compaction.decode_base64url).
    for b64 in sorted(discovered_b64s - set(feeds_by_b64)):
        padded = b64 + "=" * (4 - len(b64) % 4) if len(b64) % 4 else b64
        try:
            url = base64.urlsafe_b64decode(padded).decode("utf-8")
        except Exception:
            url = None
        con.execute(
            "INSERT INTO feeds (base64url, url, agency_id, agency_name) VALUES (?, ?, ?, ?)",
            [b64, url, "(unmapped)", "(unmapped)"],
        )

    # Arrow ingestion, not executemany: row-at-a-time inserts on millions of
    # rows would add whole minutes and hold nothing DuckDB can't take in bulk.
    if raw_files:
        cols = list(zip(*raw_files))
        raw_tbl = pa.table(
            {
                "feed_type": pa.array(cols[0], pa.string()),
                "date": pa.array(cols[1], pa.string()),
                "hour": pa.array(cols[2], pa.int8()),
                "base64url": pa.array(cols[3], pa.string()),
                "name": pa.array(cols[4], pa.string()),
                "size_bytes": pa.array(cols[5], pa.int64()),
            }
        )
        con.register("raw_files_arrow", raw_tbl)
        con.execute(
            """CREATE TABLE raw_files AS
            SELECT feed_type, CAST(date AS DATE) AS date, hour, base64url, name, size_bytes
            FROM raw_files_arrow"""
        )
    else:
        con.execute(
            """CREATE TABLE raw_files (
                feed_type VARCHAR, date DATE, hour TINYINT, base64url VARCHAR,
                name VARCHAR, size_bytes BIGINT)"""
        )

    # .meta sidecars are counted during listing (not stored as rows) to halve
    # peak memory; join those counts back in here.
    mc_rows = [(k[0], k[1], k[2], k[3], n) for k, n in meta_counts.items()]
    con.execute(
        """CREATE TABLE meta_counts (
            feed_type VARCHAR, date DATE, hour TINYINT, base64url VARCHAR,
            meta_count BIGINT)"""
    )
    if mc_rows:
        con.executemany("INSERT INTO meta_counts VALUES (?, ?, ?, ?, ?)", mc_rows)

    con.execute(PROTO_FILES_HOURLY_DDL.format(header_only_max=HEADER_ONLY_MAX))

    con.execute(
        """CREATE TABLE parquet_daily (
            feed_type VARCHAR, date DATE, base64url VARCHAR, row_count BIGINT,
            size_bytes BIGINT, num_row_groups INTEGER, path VARCHAR)"""
    )
    if parquet_daily:
        con.executemany("INSERT INTO parquet_daily VALUES (?, ?, ?, ?, ?, ?, ?)", parquet_daily)

    con.execute(
        """CREATE TABLE dropped_files (
            feed_type VARCHAR, date DATE, base64url VARCHAR, name VARCHAR,
            size_bytes BIGINT, response_code INTEGER, content_length BIGINT,
            label VARCHAR)"""
    )
    if dropped:
        con.executemany("INSERT INTO dropped_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)", dropped)

    # window_*_days and header_only_max ride along so the SQL layer derives
    # them from the run instead of restating the constants (drift there would
    # mean the dashboard annotates partitions the extract never escalated, or
    # counts a different "contentful" population than the shortfall math).
    con.execute(
        """CREATE TABLE extract_meta AS SELECT
            ? AS start_date, ? AS end_date, ? AS agency, ? AS feed_type,
            ? AS extracted_at, ? AS window_old_days, ? AS window_new_days,
            ? AS header_only_max, ? AS window_anchor_date""",
        [
            start.isoformat(),
            end.isoformat(),
            args.agency,
            args.feed_type,
            datetime.now(timezone.utc).isoformat(),
            WINDOW_OLD_DAYS,
            WINDOW_NEW_DAYS,
            HEADER_ONLY_MAX,
            # The date escalation anchored on — NOT derived from extracted_at,
            # which is stamped at write time: a run crossing UTC midnight
            # would otherwise annotate a different window than it escalated.
            today_utc.isoformat(),
        ],
    )

    con.close()
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
