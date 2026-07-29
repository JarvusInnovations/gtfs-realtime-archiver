#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "google-cloud-storage>=2.14",
#     "gcsfs>=2024.2.0",
#     "pyarrow>=16",
#     "duckdb>=1.0",
#     "gtfs-realtime-bindings>=1.0.0",
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
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

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
    parser.add_argument("--end", help="Last date, YYYY-MM-DD (default: yesterday)")
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


def list_raw_objects(client, task):
    """Full object listing (name + size) under one prefix."""
    ft, d, h, prefix, b64 = task
    rows = []
    for blob in client.list_blobs(PROTOBUF_BUCKET, prefix=prefix):
        name = blob.name
        b64_val = b64
        if b64_val is None:
            # Parse base64url from the path: .../base64url={b64}/{ts}.{ext}
            try:
                b64_val = name.split("base64url=")[1].split("/")[0]
            except IndexError:
                continue
        rows.append((ft, d, h, b64_val, name, blob.size or 0))
    return rows


def list_parquet_partitions(client, task):
    """List one {feed_type}/date={d}/ prefix of the parquet bucket."""
    ft, d = task
    rows = []
    prefix = f"{ft}/date={d}/"
    for blob in client.list_blobs(PARQUET_BUCKET, prefix=prefix):
        name = blob.name
        if not name.endswith("data.parquet"):
            continue
        try:
            b64 = name.split("base64url=")[1].split("/")[0]
        except IndexError:
            continue
        rows.append((ft, d, b64, name, blob.size or 0))
    return rows


def read_footer(fs, path: str):
    """(row_count, num_row_groups) from a parquet footer via range reads."""
    md = pq.read_metadata(f"{PARQUET_BUCKET}/{path}", filesystem=fs)
    return int(md.num_rows), int(md.num_row_groups)


def read_source_files(fs, path: str) -> set[str]:
    """Distinct source_file values from one parquet (single-column read)."""
    table = pq.read_table(f"{PARQUET_BUCKET}/{path}", columns=["source_file"], filesystem=fs)
    return set(table.column("source_file").to_pylist())


def classify_drop(client, bucket, feed_type: str, pb_name: str, size_bytes: int):
    """Label one anti-joined .pb: why did it not contribute rows?

    Reads the sibling .meta (response_code/content_length) and, since the
    anti-joined set is tiny, downloads and parses the .pb itself for the exact
    parse-failure vs zero-entities distinction the compaction loop makes.
    """
    from google.protobuf.message import DecodeError
    from google.transit import gtfs_realtime_pb2

    meta_name = pb_name.rsplit(".", 1)[0] + ".meta"
    response_code = None
    content_length = None
    try:
        meta = json.loads(bucket.blob(meta_name).download_as_text())
        response_code = meta.get("response_code")
        content_length = meta.get("content_length")
    except Exception:
        pass

    if size_bytes == 0:
        label = "never_valid_empty_body"
    else:
        try:
            content = bucket.blob(pb_name).download_as_bytes()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(content)
            entity_field = ENTITY_FIELDS[feed_type]
            n_entities = sum(1 for e in feed.entity if e.HasField(entity_field))
            label = "legitimately_empty_feed" if n_entities == 0 else "unexplained_drop"
        except DecodeError:
            label = "parse_failure"
        except Exception:
            label = "unreadable"

    return response_code, content_length, label


def main() -> int:
    args = parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=13)
    if start > end:
        print(f"--start {start} is after --end {end}", file=sys.stderr)
        return 1
    dates = date_range(start, end)
    feed_types = [args.feed_type] if args.feed_type else FEED_TYPES

    from google.cloud import storage  # deferred: slow import

    client = storage.Client()
    import gcsfs

    fs = gcsfs.GCSFileSystem()

    # 1. Feed dimension ----------------------------------------------------
    print(f"Fetching {FEEDS_PARQUET_URL}")
    feeds = fetch_feeds()
    feeds_by_b64 = {r["base64url"]: r for r in feeds.to_pylist()}
    print(f"  {feeds.num_rows} feeds in feeds.parquet")

    agency_b64s: set[str] | None = None
    if args.agency:
        agency_b64s = {r["base64url"] for r in feeds.to_pylist() if r["agency_id"] == args.agency}
        if not agency_b64s:
            known = sorted({r["agency_id"] for r in feeds.to_pylist()})
            print(
                f"No feeds for agency '{args.agency}'. Known: {', '.join(known)}", file=sys.stderr
            )
            return 1
        print(f"  --agency {args.agency}: {len(agency_b64s)} feeds")

    # 2. Raw side ----------------------------------------------------------
    hours = hour_prefixes(feed_types, dates)
    print(f"Discovery pass: {len(hours)} hour prefixes (delimiter listing)")
    discovered: list[tuple[str, str, int, str]] = []  # (ft, date, hour, b64)
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        for found in pool.map(lambda t: discover_feeds_in_hour(client, t), hours):
            discovered.extend(found)
    discovered_b64s = {b64 for _, _, _, b64 in discovered}
    print(f"  {len(discovered_b64s)} distinct feeds present in raw bucket")

    # Object listings: exact per-feed prefixes in agency mode, whole hour
    # prefixes otherwise.
    if agency_b64s is not None:
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
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        for rows in pool.map(lambda t: list_raw_objects(client, t), list_tasks):
            raw_files.extend(rows)
    print(f"  {len(raw_files)} raw objects listed")

    # 3. Parquet side (windowed by construction) ---------------------------
    pq_tasks = [(ft, d) for ft in feed_types for d in dates]
    print(f"Parquet listing: {len(pq_tasks)} date prefixes")
    pq_objects: list[tuple] = []
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        for rows in pool.map(lambda t: list_parquet_partitions(client, t), pq_tasks):
            pq_objects.extend(rows)

    # Footer reads: scope to selected feeds in agency mode.
    selected = agency_b64s if agency_b64s is not None else None
    footer_targets = [
        (ft, d, b64, path, size)
        for (ft, d, b64, path, size) in pq_objects
        if selected is None or b64 in selected
    ]
    print(f"Footer reads: {len(footer_targets)} partitions")
    parquet_daily: list[tuple] = []
    with ThreadPoolExecutor(LIST_WORKERS) as pool:
        footers = pool.map(lambda t: read_footer(fs, t[3]), footer_targets)
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
    pb_counts: dict[tuple, int] = {}
    for ft, d, h, b64, name, size in raw_files:
        if name.endswith(".pb"):
            pb_counts[(ft, d, b64)] = pb_counts.get((ft, d, b64), 0) + 1

    flagged = []
    for ft, d, b64, rows, size, groups, path in parquet_daily:
        pb_n = pb_counts.get((ft, d, b64), 0)
        if pb_n == 0:
            continue
        if rows is None or rows == 0 or (groups is not None and groups < pb_n):
            flagged.append((ft, d, b64, path))
    print(f"Escalation: {len(flagged)} flagged partitions")

    parquet_source_files: list[tuple] = []
    dropped: list[tuple] = []
    protobuf_bucket = client.bucket(PROTOBUF_BUCKET)
    raw_pb_by_partition: dict[tuple, list[tuple[str, int]]] = {}
    for ft, d, h, b64, name, size in raw_files:
        if name.endswith(".pb"):
            raw_pb_by_partition.setdefault((ft, d, b64), []).append((name, size))

    for ft, d, b64, path in flagged:
        sources: set[str] = set()
        if path is not None:
            sources = read_source_files(fs, path)
            for s in sorted(sources):
                parquet_source_files.append((ft, d, b64, s))
        for name, size in sorted(raw_pb_by_partition.get((ft, d, b64), [])):
            if name in sources:
                continue
            code, clen, label = classify_drop(client, protobuf_bucket, ft, name, size)
            dropped.append((ft, d, b64, name, size, code, clen, label))
    print(f"  {len(dropped)} dropped .pb files classified")

    # 5. Write DuckDB -------------------------------------------------------
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    con = duckdb.connect(str(DB_PATH))

    con.register("feeds_arrow", feeds)
    con.execute("CREATE TABLE feeds AS SELECT * FROM feeds_arrow")
    # Unmapped feeds: present in GCS, absent from feeds.parquet.
    for b64 in sorted(discovered_b64s - set(feeds_by_b64)):
        con.execute(
            "INSERT INTO feeds VALUES (?, ?, ?, ?, ?, ?, ?)",
            [b64, None, None, "(unmapped)", "(unmapped)", None, None],
        )

    con.execute(
        """CREATE TABLE raw_files (
            feed_type VARCHAR, date DATE, hour TINYINT, base64url VARCHAR,
            name VARCHAR, size_bytes BIGINT)"""
    )
    if raw_files:
        con.executemany("INSERT INTO raw_files VALUES (?, ?, ?, ?, ?, ?)", raw_files)

    con.execute(
        """CREATE TABLE proto_files_hourly AS
        SELECT feed_type, date, hour, base64url,
               count(*) FILTER (name LIKE '%.pb') AS pb_count,
               count(*) FILTER (name LIKE '%.meta') AS meta_count,
               count(*) FILTER (name LIKE '%.pb' AND size_bytes = 0) AS zero_byte_count
        FROM raw_files GROUP BY ALL"""
    )

    con.execute(
        """CREATE TABLE parquet_daily (
            feed_type VARCHAR, date DATE, base64url VARCHAR, row_count BIGINT,
            size_bytes BIGINT, num_row_groups INTEGER, path VARCHAR)"""
    )
    if parquet_daily:
        con.executemany("INSERT INTO parquet_daily VALUES (?, ?, ?, ?, ?, ?, ?)", parquet_daily)

    con.execute(
        """CREATE TABLE parquet_source_files (
            feed_type VARCHAR, date DATE, base64url VARCHAR, source_file VARCHAR)"""
    )
    if parquet_source_files:
        con.executemany(
            "INSERT INTO parquet_source_files VALUES (?, ?, ?, ?)", parquet_source_files
        )

    con.execute(
        """CREATE TABLE dropped_files (
            feed_type VARCHAR, date DATE, base64url VARCHAR, name VARCHAR,
            size_bytes BIGINT, response_code INTEGER, content_length BIGINT,
            label VARCHAR)"""
    )
    if dropped:
        con.executemany("INSERT INTO dropped_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)", dropped)

    con.execute(
        """CREATE TABLE extract_meta AS SELECT
            ? AS start_date, ? AS end_date, ? AS agency, ? AS feed_type,
            ? AS extracted_at""",
        [
            start.isoformat(),
            end.isoformat(),
            args.agency,
            args.feed_type,
            datetime.now(timezone.utc).isoformat(),
        ],
    )

    con.close()
    print(f"Wrote {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
