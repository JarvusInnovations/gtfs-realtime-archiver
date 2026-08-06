"""Error-contract tests for compact_single_feed (PR #94 review rounds 3-7).

The per-file error handling was reshaped during review: parse failures
(DecodeError/ValueError) skip the file with a warning, while Parquet
conversion/write failures fail the whole partition as dg.Failure naming the
offending file — and nothing is uploaded. These pin both halves so a future
refactor collapsing the parse handler back to a broad `except Exception`
cannot silently reintroduce the empty-partition bug.
"""

import io
from typing import Any

import dagster as dg
import pyarrow.parquet as pq
import pytest
from google.transit import gtfs_realtime_pb2

from dagster_pipeline.defs.assets import compaction
from dagster_pipeline.defs.assets.schemas import VEHICLE_POSITIONS_SCHEMA
from dagster_pipeline.defs.resources import GCSResource

PARTITION = dg.MultiPartitionKey({"date": "2026-07-01", "feed": "example.com/feed"})


class _FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self._store = store
        self._name = name

    def download_as_bytes(self) -> bytes:
        return self._store[self._name]

    def download_as_text(self) -> str:
        # .meta files absent — read_meta_file swallows this to None
        raise FileNotFoundError(self._name)

    def upload_from_file(self, fileobj: Any, **_kwargs: Any) -> None:
        self._store[self._name] = fileobj.read()


class _FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self._store, name)


class _FakeClient:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def bucket(self, _name: str) -> _FakeBucket:
        return _FakeBucket(self._store)


def _vp_feed_bytes(entity_id: str) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_754_000_000
    entity = feed.entity.add()
    entity.id = entity_id
    entity.vehicle.position.latitude = 1.0
    entity.vehicle.position.longitude = 2.0
    return feed.SerializeToString()


def _run(
    monkeypatch: pytest.MonkeyPatch,
    store: dict[str, bytes],
    pb_files: list[str],
    extractor: Any,
) -> dg.Output[dict[str, int]]:
    monkeypatch.setattr(compaction, "list_pb_files", lambda *_a, **_k: pb_files)
    monkeypatch.setattr(GCSResource, "get_client", lambda _self: _FakeClient(store))
    gcs = GCSResource(protobuf_bucket="pb-bucket", parquet_bucket="pq-bucket")
    context = dg.build_asset_context(partition_key=PARTITION)
    return compaction.compact_single_feed(
        context, gcs, "vehicle_positions", VEHICLE_POSITIONS_SCHEMA, extractor
    )


def test_decode_error_file_skipped_rest_of_partition_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file that fails protobuf parsing is skipped with a warning; the
    remaining files still produce a complete parquet upload — one row group
    per non-empty file (the reconciliation invariant)."""
    store = {
        "f1.pb": _vp_feed_bytes("v1"),
        "bad.pb": b"\x08",  # truncated varint -> DecodeError
        "f3.pb": _vp_feed_bytes("v3"),
    }
    result = _run(
        monkeypatch, store, ["f1.pb", "bad.pb", "f3.pb"], compaction.extract_vehicle_positions
    )

    assert result.value == {"files_processed": 3, "records_written": 2, "files_failed": 1}
    uploaded = [k for k in store if k.endswith("data.parquet")]
    assert len(uploaded) == 1
    table = pq.read_table(io.BytesIO(store[uploaded[0]]))
    assert table.num_rows == 2
    assert set(table.column("entity_id").to_pylist()) == {"v1", "v3"}
    metadata = pq.ParquetFile(io.BytesIO(store[uploaded[0]])).metadata
    assert metadata.num_row_groups == 2  # one per non-empty .pb


def test_conversion_failure_fails_partition_naming_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schema/type bug in extracted records must NOT masquerade as a
    per-file parse warning over an empty partition: it raises dg.Failure
    carrying the offending file's name, and nothing is uploaded."""
    store = {"f1.pb": _vp_feed_bytes("v1"), "poison.pb": _vp_feed_bytes("v2")}

    def bad_extractor(_feed: Any, source_file: str, feed_url: str, _ts: Any) -> Any:
        latitude: Any = "not-a-float" if source_file == "poison.pb" else 1.0
        yield {
            "source_file": source_file,
            "feed_url": feed_url,
            "entity_id": "x",
            "latitude": latitude,
        }

    with pytest.raises(dg.Failure, match="poison.pb"):
        _run(monkeypatch, store, ["f1.pb", "poison.pb"], bad_extractor)

    assert not [k for k in store if k.endswith("data.parquet")]


def _breaking_close(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_self: Any) -> None:
        # Clear is_open so ParquetWriter.__del__ doesn't re-invoke this
        # patched close during GC and leak the error into pytest's
        # unraisable-exception hook.
        _self.is_open = False
        raise RuntimeError("footer flush failed")

    monkeypatch.setattr(pq.ParquetWriter, "close", boom)


def test_close_failure_on_success_path_propagates_and_blocks_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If writer.close() fails after a clean loop, the error must propagate
    (not be logged away) and nothing may be uploaded — a swallowed close
    would ship a parquet with a missing/partial footer."""
    _breaking_close(monkeypatch)
    store = {"f1.pb": _vp_feed_bytes("v1")}

    with pytest.raises(RuntimeError, match="footer flush failed"):
        _run(monkeypatch, store, ["f1.pb"], compaction.extract_vehicle_positions)

    assert not [k for k in store if k.endswith("data.parquet")]


def test_close_failure_does_not_bury_in_flight_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If writer.close() fails while a dg.Failure is already unwinding, the
    original Failure — carrying the offending file's name — must survive;
    the close error is only logged."""
    _breaking_close(monkeypatch)
    store = {"f1.pb": _vp_feed_bytes("v1"), "poison.pb": _vp_feed_bytes("v2")}

    def bad_extractor(_feed: Any, source_file: str, feed_url: str, _ts: Any) -> Any:
        latitude: Any = "not-a-float" if source_file == "poison.pb" else 1.0
        yield {
            "source_file": source_file,
            "feed_url": feed_url,
            "entity_id": "x",
            "latitude": latitude,
        }

    with pytest.raises(dg.Failure, match="poison.pb"):
        _run(monkeypatch, store, ["f1.pb", "poison.pb"], bad_extractor)

    assert not [k for k in store if k.endswith("data.parquet")]
