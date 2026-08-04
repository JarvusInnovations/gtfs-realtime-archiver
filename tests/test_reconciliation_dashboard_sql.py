"""Executable tests of the reconciliation dashboard's SQL.

Builds a small synthetic DuckDB in the extract's schema, runs every file in
sources/archiver/, then runs the page's decision-bearing queries (with
Evidence ``${inputs.*}`` placeholders substituted) and asserts the emitted
diagnosis/remediate strings for each scenario. This exercises what the
drift-guard text tests cannot: whether the SQL actually gives the right
advice.

Scenarios (one partition each, date chosen inside/outside the reconcilable
window around the fixture's anchor date 2026-08-01, window = anchor-358 ..
anchor-2):

- quiet-missing:   all header-only files, no parquet  -> excluded entirely
- busted-missing:  one parse_failure file, no parquet -> do NOT re-run, no command
- valid-missing:   one unexplained_drop, no parquet   -> remediation recovers, command present
- errored-missing: classification errored, no parquet -> incomplete, command present
- stale-missing:   out of window, no parquet          -> labeled, no command
- short:           parquet short of contentful count  -> shortfall explained
"""

import importlib.util
import re
from pathlib import Path

import duckdb
import pytest

DASHBOARD = Path(__file__).parents[1] / "dashboards" / "proto-parquet-reconciliation"
SOURCES = DASHBOARD / "sources" / "archiver"
PAGE_SRC = (DASHBOARD / "pages" / "index.md").read_text()

_spec = importlib.util.spec_from_file_location("recon_extract_sql", DASHBOARD / "extract.py")
assert _spec is not None and _spec.loader is not None
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)

ANCHOR = "2026-08-01"
IN_WINDOW = "2026-07-25"
OUT_WINDOW = "2026-07-31"  # anchor-1: newer than the 2-day edge
# One fixture threshold, used both in extract_meta (which drop_summary derives
# from) and in the DDL .format() — the fixture sizes (15/41/300) are designed
# around it.
HEADER_ONLY = 20

B64 = {
    "quiet": "cXVpZXQ",
    "busted": "YnVzdGVk",
    "valid": "dmFsaWQ",
    "errored": "ZXJyb3JlZA",
    "stale": "c3RhbGU",
    "short": "c2hvcnQ",
}


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("""
        CREATE TABLE feeds (
            base64url VARCHAR, url VARCHAR, feed_type VARCHAR, agency_id VARCHAR,
            agency_name VARCHAR, system_id VARCHAR, system_name VARCHAR)
    """)
    for key, b64 in B64.items():
        c.execute(
            "INSERT INTO feeds VALUES (?, ?, 'service_alerts', ?, ?, NULL, NULL)",
            [b64, f"https://{key}.example.com/alerts", key, f"{key.title()} Transit"],
        )

    c.execute("""
        CREATE TABLE raw_files (
            feed_type VARCHAR, date DATE, hour TINYINT, base64url VARCHAR,
            name VARCHAR, size_bytes BIGINT)
    """)
    c.execute("""
        CREATE TABLE meta_counts (
            feed_type VARCHAR, date DATE, hour TINYINT, base64url VARCHAR,
            meta_count BIGINT)
    """)

    def raw(key: str, date: str, sizes: list[int]) -> None:
        for i, size in enumerate(sizes):
            c.execute(
                "INSERT INTO raw_files VALUES ('service_alerts', ?, 6, ?, ?, ?)",
                [date, B64[key], f"service_alerts/date={date}/x/{key}-{i}.pb", size],
            )

    raw("quiet", IN_WINDOW, [15, 15, 15])  # header-only: healthy, not missing
    raw("busted", IN_WINDOW, [15, 41])  # one contentful file, and it's garbage
    raw("valid", IN_WINDOW, [15, 500])  # one contentful valid file, dropped
    raw("errored", IN_WINDOW, [400])  # classification errored
    raw("stale", OUT_WINDOW, [300])  # outside the reconcilable window
    raw("short", IN_WINDOW, [300, 300, 300])  # 3 contentful, 2 row groups

    c.execute("""
        CREATE TABLE parquet_daily (
            feed_type VARCHAR, date DATE, base64url VARCHAR, row_count BIGINT,
            size_bytes BIGINT, num_row_groups INTEGER, path VARCHAR)
    """)
    # missing partitions: null rows, no path
    for key, date in [
        ("busted", IN_WINDOW),
        ("valid", IN_WINDOW),
        ("errored", IN_WINDOW),
        ("stale", OUT_WINDOW),
    ]:
        c.execute(
            "INSERT INTO parquet_daily VALUES ('service_alerts', ?, ?, NULL, NULL, NULL, NULL)",
            [date, B64[key]],
        )
    c.execute(
        "INSERT INTO parquet_daily VALUES ('service_alerts', ?, ?, 120, 9000, 2, 'p/short')",
        [IN_WINDOW, B64["short"]],
    )

    c.execute("""
        CREATE TABLE dropped_files (
            feed_type VARCHAR, date DATE, base64url VARCHAR, name VARCHAR,
            size_bytes BIGINT, response_code INTEGER, content_length BIGINT,
            label VARCHAR)
    """)
    drops = [
        ("busted", 41, "parse_failure"),
        ("valid", 500, "unexplained_drop"),
        ("errored", 400, "error"),
        ("short", 300, "parse_failure"),
    ]
    for key, size, label in drops:
        c.execute(
            "INSERT INTO dropped_files VALUES ('service_alerts', ?, ?, ?, ?, 200, ?, ?)",
            [IN_WINDOW, B64[key], f"service_alerts/{key}.pb", size, size, label],
        )

    c.execute(
        """CREATE TABLE extract_meta AS SELECT
            '2026-07-18' AS start_date, '2026-07-31' AS end_date,
            NULL AS agency, NULL AS feed_type,
            '2026-08-01T10:00:00+00:00' AS extracted_at,
            358 AS window_old_days, 2 AS window_new_days,
            ? AS header_only_max, ? AS window_anchor_date""",
        [HEADER_ONLY, ANCHOR],
    )

    # proto_files_hourly is a table the extract derives from raw_files +
    # meta_counts; use the extract's own DDL so this fixture can't validate a
    # stale shape.
    c.execute(extract.PROTO_FILES_HOURLY_DDL.format(header_only_max=HEADER_ONLY))

    # Materialize every Evidence source as a view named archiver_<name>,
    # mirroring how the page addresses them (archiver.<name>).
    for f in sorted(SOURCES.glob("*.sql")):
        c.execute(f"CREATE VIEW archiver_{f.stem} AS {f.read_text()}")
    return c


def _page_query(name: str) -> str:
    m = re.search(rf"```sql {name}\n(.*?)```", PAGE_SRC, re.S)
    assert m is not None, f"page query {name} not found"
    q = re.sub(r"archiver\.(\w+)", r"archiver_\1", m.group(1))
    q = q.replace("${inputs.agency.value}", "%")
    q = q.replace("${inputs.feed_type.value}", "%")
    q = q.replace("${inputs.rpp_feed.value}", "%")
    return q


def _missing_rows(con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    cols = [
        "feed_type",
        "date",
        "agency_name",
        "system_name",
        "pb_contentful_count",
        "row_count",
        "status",
        "diagnosis",
        "remediate",
    ]
    out = {}
    for row in con.execute(_page_query("missing_partitions")).fetchall():
        rec = dict(zip(cols, row, strict=True))
        out[str(rec["agency_name"]).split(" ")[0].lower()] = rec
    return out


def test_quiet_partition_excluded(con: duckdb.DuckDBPyConnection) -> None:
    assert "quiet" not in _missing_rows(con)


def test_busted_partition_says_do_not_rerun(con: duckdb.DuckDBPyConnection) -> None:
    rec = _missing_rows(con)["busted"]
    assert rec["status"] == "MISSING"
    assert "do NOT re-run" in rec["diagnosis"]
    assert rec["remediate"] is None


def test_valid_drop_offers_remediation(con: duckdb.DuckDBPyConnection) -> None:
    rec = _missing_rows(con)["valid"]
    assert "remediation recovers real data" in rec["diagnosis"]
    assert rec["remediate"] is not None
    # the composite partition key: date|scheme-stripped url
    assert f"--partition '{IN_WINDOW}|valid.example.com/alerts'" in rec["remediate"]


def test_errored_classification_is_not_reported_empty(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rec = _missing_rows(con)["errored"]
    assert "classification incomplete" in rec["diagnosis"]
    assert rec["remediate"] is not None  # unknown must never suppress remediation


def test_out_of_window_is_labeled_not_contradictory(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rec = _missing_rows(con)["stale"]
    assert rec["status"] == "out of window"
    assert rec["diagnosis"] == "outside reconcilable window — not classified"
    assert rec["remediate"] is None


def test_feeds_ingest_dedupes_duplicates() -> None:
    """A duplicated (url, feed_type) in agencies.yaml must not fan out the
    dashboard joins: the extract's ingest statement dedupes at the source of
    truth (Evidence sources run standalone, so a dedupe there protects
    nothing)."""
    import pyarrow as pa

    c = duckdb.connect()
    feeds_arrow = pa.table(
        {
            "base64url": ["ZHVw", "ZHVw", "b3RoZXI"],
            "url": ["https://dup.example/f"] * 2 + ["https://other.example/f"],
            "feed_type": ["service_alerts"] * 3,
            "agency_id": ["a1", "a2", "a3"],
            "agency_name": ["A1", "A2", "A3"],
            "system_id": [None, None, None],
            "system_name": [None, None, None],
        }
    )
    c.register("feeds_arrow", feeds_arrow)
    c.execute(extract.FEEDS_INGEST_SQL)
    counts = dict(c.execute("select base64url, count(*) from feeds group by 1").fetchall())
    assert counts == {"ZHVw": 1, "b3RoZXI": 1}
    # the ORDER BY makes the survivor deterministic — pin it
    survivor = c.execute("select agency_id from feeds where base64url = 'ZHVw'").fetchone()[0]
    assert survivor == "a1"


def test_hourly_spine_renders_total_outage_day(con: duckdb.DuckDBPyConnection) -> None:
    """The prose promises a day with zero archived files still renders as 24
    zero cells rather than vanishing off the axis."""
    rows = con.execute(_page_query("hourly")).fetchall()
    outage_day = [(h, pb) for (d, h, pb) in rows if d == "2026-07-20"]
    assert len(outage_day) == 24
    assert all(pb == 0 for _h, pb in outage_day)


def test_short_partition_arithmetic_closes(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(_page_query("short_partitions")).fetchall()
    assert len(rows) == 1
    (
        _ft,
        _date,
        _agency,
        _system,
        contentful,
        groups,
        shortfall,
        zero_entity,
        parse_failure,
        valid_dropped,
        errored,
        unclassified,
        _row_count,
    ) = rows[0]
    assert (contentful, groups, shortfall) == (3, 2, 1)
    assert (parse_failure, unclassified) == (1, 0)
