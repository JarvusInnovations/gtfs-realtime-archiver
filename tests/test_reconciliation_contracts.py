"""Drift guards for the proto-parquet reconciliation dashboard.

dashboards/proto-parquet-reconciliation/ re-implements several pipeline
formats it cannot import (extract.py is a standalone PEP 723 script; the page
is Evidence SQL). Each copy carries a "must not drift" comment; these tests
make that comment enforceable. They read the dashboard files as text — the
dashboard's dependencies are deliberately not part of this project's
environment.
"""

from datetime import UTC, datetime
from pathlib import Path

from dagster_pipeline.defs.assets.compaction import (
    encode_base64url,
    url_to_partition_key,
)
from dagster_pipeline.defs.assets.inventory import _RT_PATTERN

DASHBOARD = Path(__file__).parents[1] / "dashboards" / "proto-parquet-reconciliation"
EXTRACT_SRC = (DASHBOARD / "extract.py").read_text()
PAGE_SRC = (DASHBOARD / "pages" / "index.md").read_text()


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
    assert "'~' || substr(url, 8)" in PAGE_SRC
    assert "regexp_replace(url, '^https://', '')" in PAGE_SRC


def test_page_derives_thresholds_from_extract_meta() -> None:
    """No restated literals: the page must derive the header-only threshold
    and the reconcilable window from extract_meta, not hardcode them."""
    assert "size_bytes > 20" not in PAGE_SRC
    assert PAGE_SRC.count("select header_only_max from archiver.extract_meta") >= 2
    assert "- 358" not in PAGE_SRC and "- 2\n" not in PAGE_SRC


def test_base64url_padding_matches_compaction() -> None:
    """extract.py decodes unmapped feeds' base64url with the same padding
    expression compaction uses to encode/decode."""
    url = "https://example.com/gtfs-rt?x=1"
    b64 = encode_base64url(url)
    padded = b64 + "=" * (4 - len(b64) % 4) if len(b64) % 4 else b64
    import base64

    assert base64.urlsafe_b64decode(padded).decode() == url
    assert 'b64 + "=" * (4 - len(b64) % 4) if len(b64) % 4 else b64' in EXTRACT_SRC
