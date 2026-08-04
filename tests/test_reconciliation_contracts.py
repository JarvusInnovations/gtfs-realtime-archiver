"""Drift guards and behavioral tests for the reconciliation dashboard.

dashboards/proto-parquet-reconciliation/ re-implements several pipeline
formats it cannot import (extract.py is a standalone PEP 723 script; the page
is Evidence SQL). Each copy carries a "must not drift" comment; the guard
tests make those comments enforceable. extract.py defers its one
non-dev-environment import (duckdb) so its pure helpers can also be exercised
behaviorally here.
"""

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from dagster_pipeline.defs.assets.compaction import (
    encode_base64url,
    url_to_partition_key,
)
from dagster_pipeline.defs.assets.inventory import _RT_PATTERN

DASHBOARD = Path(__file__).parents[1] / "dashboards" / "proto-parquet-reconciliation"
EXTRACT_SRC = (DASHBOARD / "extract.py").read_text()
PAGE_SRC = (DASHBOARD / "pages" / "index.md").read_text()

_spec = importlib.util.spec_from_file_location("recon_extract", DASHBOARD / "extract.py")
assert _spec is not None and _spec.loader is not None
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


def _norm(s: str) -> str:
    return " ".join(s.split())


def test_rt_pattern_copied_verbatim() -> None:
    """extract.py's parquet-path regex must equal inventory._RT_PATTERN.

    Parsed via ast rather than text search: the copy is split across
    implicitly-concatenated string literals, which the parser resolves.
    """
    import ast

    pattern = None
    for node in ast.walk(ast.parse(EXTRACT_SRC)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_RT_PATTERN" for t in node.targets
        ):
            call = node.value
            assert isinstance(call, ast.Call)
            arg = call.args[0]
            assert isinstance(arg, ast.Constant)
            pattern = arg.value
    assert pattern == _RT_PATTERN.pattern


def test_hour_prefix_format_matches_storage() -> None:
    """extract.py constructs hour partition values; storage.py defines them.

    storage.generate_storage_path uses strftime("%Y-%m-%dT%H:00:00Z"); the
    extract builds the same segment as f"hour={d}T{h:02d}:00:00Z".
    """
    ts = datetime(2026, 1, 2, 3, 45, tzinfo=UTC)
    storage_hour = ts.strftime("%Y-%m-%dT%H:00:00Z")
    d, h = "2026-01-02", 3
    extract_hour = f"{d}T{h:02d}:00:00Z"
    assert extract_hour == storage_hour
    assert "hour={d}T{h:02d}:00:00Z" in EXTRACT_SRC


def test_page_feed_key_sql_mirrors_url_to_partition_key() -> None:
    """The page builds dg-launch partition keys in SQL; verify both the
    reference implementation's behavior and that the SQL snippets are intact.
    """
    assert url_to_partition_key("https://a.example/feed") == "a.example/feed"
    assert url_to_partition_key("http://a.example/feed") == "~a.example/feed"
    # https:// is 8 chars — the SQL strips it via regexp_replace; http:// is
    # 7 chars — the SQL takes substr(url, 8) (1-indexed) and prepends ~.
    # Whitespace-normalized so SQL reformatting can't false-fail the guard.
    assert "'~' || substr(url, 8)" in _norm(PAGE_SRC)
    assert "regexp_replace(url, '^https://', '')" in _norm(PAGE_SRC)


def test_drop_summary_derives_threshold_from_extract_meta() -> None:
    """No restated literals: the shared drop rollup derives the header-only
    threshold from extract_meta, and the page never hardcodes it."""
    ds_src = (DASHBOARD / "sources" / "archiver" / "drop_summary.sql").read_text()
    assert "select header_only_max from extract_meta" in _norm(ds_src)
    # the page may display size_bytes but never applies a size threshold —
    # that logic lives in the sources, derived from extract_meta
    assert "size_bytes >" not in PAGE_SRC


def test_daily_comparison_derives_window_from_meta() -> None:
    """The reconcilable-window arithmetic lives in daily_comparison.sql; it
    must derive the bounds from extract_meta, never restate 358/2."""
    dc_src = (DASHBOARD / "sources" / "archiver" / "daily_comparison.sql").read_text()
    assert "select window_old_days from meta" in dc_src
    assert "select window_new_days from meta" in dc_src
    assert "358" not in dc_src


def test_prose_missing_classify_max_matches_extract() -> None:
    """The page prose states the low-volume classification threshold as a
    number; pin it to extract.py's MISSING_CLASSIFY_MAX."""
    import ast
    import re

    value = None
    for node in ast.walk(ast.parse(EXTRACT_SRC)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "MISSING_CLASSIFY_MAX" for t in node.targets
        ):
            assert isinstance(node.value, ast.Constant)
            value = node.value.value
    assert value is not None
    m = re.search(r"≤(\d+) contentful files", PAGE_SRC)
    assert m is not None, "page prose no longer states the threshold"
    assert int(m.group(1)) == value


def test_base64url_padding_matches_compaction() -> None:
    """extract.py decodes unmapped feeds' base64url with the same padding
    expression compaction uses to encode/decode."""
    url = "https://example.com/gtfs-rt?x=1"
    b64 = encode_base64url(url)
    padded = b64 + "=" * (4 - len(b64) % 4) if len(b64) % 4 else b64
    import base64

    assert base64.urlsafe_b64decode(padded).decode() == url
    assert 'b64 + "=" * (4 - len(b64) % 4) if len(b64) % 4 else b64' in _norm(EXTRACT_SRC)


@pytest.mark.parametrize("script", ["extract.py", "unpack.py"])
def test_pep723_bindings_floor_not_below_root(script: str) -> None:
    """Both scripts claim parse parity with compaction, which only holds if
    they can resolve the same generated bindings: the PEP 723 floor must
    never fall below the root project's."""
    import re

    src = (DASHBOARD / script).read_text()
    m = re.search(r'"gtfs-realtime-bindings>=([\d.]+)"', src)
    assert m is not None
    script_floor = tuple(int(x) for x in m.group(1).split("."))
    root_src = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    m2 = re.search(r'"gtfs-realtime-bindings>=([\d.]+)"', root_src)
    assert m2 is not None
    root_floor = tuple(int(x) for x in m2.group(1).split("."))
    assert script_floor >= root_floor


def test_date_range_edges() -> None:
    assert extract.date_range(date(2026, 1, 1), date(2026, 1, 3)) == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]
    assert extract.date_range(date(2026, 1, 1), date(2026, 1, 1)) == ["2026-01-01"]


def test_with_retries_short_circuits_terminal_errors() -> None:
    from google.api_core.exceptions import NotFound

    calls: list[int] = []

    def boom() -> None:
        calls.append(1)
        raise NotFound("gone")

    with pytest.raises(NotFound):
        extract.with_retries(boom)()
    assert len(calls) == 1  # no retries burned on a guaranteed failure


def test_with_retries_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract.time, "sleep", lambda _s: None)
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return "ok"

    assert extract.with_retries(flaky)() == "ok"
    assert len(calls) == 3


class _FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self.store, self.name = store, name

    def download_as_bytes(self) -> bytes:
        return self.store[self.name]

    def download_as_text(self) -> str:
        raise FileNotFoundError(self.name)  # no .meta sidecar in fixtures


class _FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self.store, name)


def _feed_message(n_vehicles: int) -> bytes:
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_700_000_000
    for i in range(n_vehicles):
        entity = feed.entity.add()
        entity.id = str(i)
        entity.vehicle.vehicle.id = f"v{i}"
    return feed.SerializeToString()


def test_classify_drop_label_mapping() -> None:
    """Pin the three labels, including the exact fleet-observed failure body
    (the HTTP-200 BusTime error text) as the parse_failure case."""
    store = {
        "valid.pb": _feed_message(2),
        "empty.pb": _feed_message(0),
        "garbage.pb": b"ERROR: no connectivity to BusTime server!",
    }
    bucket = _FakeBucket(store)
    labels = {name: extract.classify_drop(bucket, "vehicle_positions", name)[2] for name in store}
    assert labels == {
        "valid.pb": "unexplained_drop",
        "empty.pb": "legitimately_empty_feed",
        "garbage.pb": "parse_failure",
    }


def test_read_source_files_dictionary_chunks(tmp_path: Path) -> None:
    """Exercise the dictionary-encoded chunk branch against a local parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "part.parquet"
    table = pa.table({"source_file": pa.array(["a.pb"] * 500 + ["b.pb"] * 500)})
    pq.write_table(table, path)
    assert extract.read_source_files(str(path)) == {"a.pb", "b.pb"}
