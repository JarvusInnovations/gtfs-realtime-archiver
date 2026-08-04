#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "google-cloud-storage>=2.14",
#     "gcsfs>=2024.2.0",
#     "pyarrow>=22",
#     "gtfs-realtime-bindings>=2.2.0",
# ]
# ///
"""Manually inspect one .pb and its neighbors against the compacted parquet.

Given a raw .pb path (copy it from the dashboard's dropped-files table), this
downloads a window of consecutive protobuf snapshots around it, parses each to
human-readable JSON, pulls the matching rows out of the partition's parquet
(public bucket, row-group-pruned — not the whole file), and writes everything
under .scratch/inspect/ for side-by-side eyeballing.

Run from the repo root:
    uv run --script dashboards/proto-parquet-reconciliation/unpack.py \
        --pb 'trip_updates/date=2026-07-06/hour=.../base64url=.../<ts>.pb'

Temporary tooling, same caveats as extract.py. Requires ADC (raw bucket is
private); the parquet side is public.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

PROTOBUF_BUCKET = os.environ.get("GCS_BUCKET_RT_PROTOBUF", "protobuf.gtfsrt.io")
PARQUET_BUCKET = os.environ.get("GCS_BUCKET_RT_PARQUET", "parquet.gtfsrt.io")

_PB_PATTERN = re.compile(
    r"^(?P<feed_type>[^/]+)/date=(?P<date>\d{4}-\d{2}-\d{2})"
    r"/hour=(?P<hour>[^/]+)/base64url=(?P<base64url>[A-Za-z0-9_-]+)"
    r"/(?P<ts>[^/]+)\.pb$"
)

ENTITY_FIELDS = {
    "vehicle_positions": "vehicle",
    "trip_updates": "trip_update",
    "service_alerts": "alert",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pb", required=True, help="Full raw .pb object path")
    parser.add_argument(
        "--count", type=int, default=5, help="Window size, centered on --pb (default 5)"
    )
    parser.add_argument(
        "--out", type=Path, default=Path(".scratch/inspect"), help="Output root directory"
    )
    args = parser.parse_args()

    match = _PB_PATTERN.match(args.pb)
    if not match:
        print(f"--pb does not look like a raw .pb path: {args.pb}", file=sys.stderr)
        return 1
    ft, d, hour, b64, ts = match.group("feed_type", "date", "hour", "base64url", "ts")

    from google.cloud import storage
    from google.protobuf import json_format
    from google.protobuf.message import DecodeError
    from google.transit import gtfs_realtime_pb2

    client = storage.Client()
    bucket = client.bucket(PROTOBUF_BUCKET)

    # Consecutive = neighbors in the same hour prefix, sorted by timestamp name.
    prefix = f"{ft}/date={d}/hour={hour}/base64url={b64}/"
    names = sorted(b.name for b in client.list_blobs(PROTOBUF_BUCKET, prefix=prefix))
    pb_names = [n for n in names if n.endswith(".pb")]
    if args.pb not in pb_names:
        print(f"{args.pb} not found under {prefix}", file=sys.stderr)
        return 1
    i = pb_names.index(args.pb)
    half = args.count // 2
    lo = max(0, i - half)
    window = pb_names[lo : lo + args.count]

    outdir = args.out / f"{ft}_{d}_{ts.replace(':', '')}"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"Writing to {outdir}/")

    summary = []
    for name in window:
        base = name.rsplit("/", 1)[1]
        marker = "  <-- the file you asked about" if name == args.pb else ""
        content = bucket.blob(name).download_as_bytes()
        (outdir / base).write_bytes(content)

        # Sidecar .meta: fetch-time HTTP facts, tiny and always relevant.
        meta_name = name.rsplit(".", 1)[0] + ".meta"
        try:
            (outdir / (base + ".meta.json")).write_text(bucket.blob(meta_name).download_as_text())
        except Exception:
            pass

        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(content)
            n_entities = sum(1 for e in feed.entity if e.HasField(ENTITY_FIELDS[ft]))
            (outdir / (base + ".json")).write_text(
                json_format.MessageToJson(feed, preserving_proto_field_name=True)
            )
            summary.append((base, len(content), f"parses OK, {n_entities} entities", marker))
        # (DecodeError, ValueError) matches compaction.py's parse-failure set —
        # a ValueError file is exactly what this tool gets pointed at.
        except (DecodeError, ValueError) as e:
            (outdir / (base + ".PARSE_ERROR.txt")).write_text(
                f"{type(e).__name__}: {e}\n\nsize: {len(content)} bytes"
                f" ({len(content) / 4096:.2f} x 4096)\n"
                f"first 32 bytes: {content[:32]!r}\nlast 32 bytes: {content[-32:]!r}\n"
            )
            summary.append((base, len(content), f"PARSE ERROR: {e}", marker))

    # Matching parquet rows: filter on source_file — one row group per source
    # file means this reads only the window's row groups, not the whole file.
    import gcsfs

    fs = gcsfs.GCSFileSystem()
    pq_path = f"{PARQUET_BUCKET}/{ft}/date={d}/base64url={b64}/data.parquet"
    try:
        rows = pq.read_table(pq_path, filesystem=fs, filters=[("source_file", "in", window)])
        per_file = {}
        for sf in rows.column("source_file").to_pylist():
            per_file[sf] = per_file.get(sf, 0) + 1
        # Slim the CSV for human (and editor) consumption: the constant
        # feed_url and the ~150-char source_file path repeated per row double
        # the file size, and VS Code stops colorizing files above 20MB.
        slim = {}
        for col_name in rows.column_names:
            if col_name == "feed_url":
                continue
            col = rows.column(col_name)
            if col_name == "source_file":
                slim["snapshot"] = pc.replace_substring_regex(col, r"^.*/", "")
            else:
                slim[col_name] = col
        pacsv.write_csv(pa.table(slim), outdir / "parquet_rows.csv")
    except FileNotFoundError:
        rows = None
        per_file = {}
        print(f"NOTE: no parquet exists at gs://{pq_path} (missing partition)")

    print()
    print(f"{'snapshot':<32} {'bytes':>8}  {'pb parse':<40} parquet rows")
    for base, size, status, marker in summary:
        full = f"{ft}/date={d}/hour={hour}/base64url={b64}/{base}"
        print(f"{base:<32} {size:>8}  {status:<40} {per_file.get(full, 0)}{marker}")
    if rows is not None:
        print(f"\nparquet_rows.csv: {rows.num_rows} rows across the window")
    print(f"\nFiles in {outdir}/: raw .pb, parsed .json (or .PARSE_ERROR.txt),")
    print("  .meta.json sidecars, parquet_rows.csv")
    print(f"Full partition parquet: https://storage.googleapis.com/{pq_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
