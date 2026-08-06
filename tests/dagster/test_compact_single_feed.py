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


def test_all_files_failed_parse_fails_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every file failing to parse is systemic (garbage feed content or a
    parser regression), not per-file flakiness: the partition must raise
    dg.Failure instead of reporting a green zero-record run whose only
    trace is metadata (PR #94 round 13). Partial failure staying per-file
    is pinned by test_decode_error_file_skipped_rest_of_partition_written."""
    store = {"bad1.pb": b"\x08", "bad2.pb": b"\xff\xff"}

    with pytest.raises(dg.Failure, match=r"All 2 \.pb files failed to parse"):
        _run(monkeypatch, store, ["bad1.pb", "bad2.pb"], compaction.extract_vehicle_positions)

    assert not [k for k in store if k.endswith("data.parquet")]


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


def _mixed_tu_feed_bytes() -> bytes:
    """A trip_updates snapshot carrying all four captured entity types —
    the Madison/BBB shape (2026-08-04 census)."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_754_000_000
    e = feed.entity.add()
    e.id = "tu-1"
    e.trip_update.trip.trip_id = "trip-1"
    e.trip_update.stop_time_update.add().stop_id = "s1"
    e = feed.entity.add()
    e.id = "tm-1"
    st = e.trip_modifications.selected_trips.add()
    st.trip_ids.append("trip-1")
    st.shape_id = "shape-1"
    e = feed.entity.add()
    e.id = "shape-1"
    e.shape.shape_id = "shape-1"
    e.shape.encoded_polyline = "abc"
    e = feed.entity.add()
    e.id = "stop-1"
    e.stop.stop_id = "st-1"
    return feed.SerializeToString()


def _tu_only_feed_bytes(entity_id: str) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_754_000_000
    e = feed.entity.add()
    e.id = entity_id
    e.trip_update.trip.trip_id = "trip-x"
    e.trip_update.stop_time_update.add().stop_id = "s1"
    return feed.SerializeToString()


def _run_tables(
    monkeypatch: pytest.MonkeyPatch,
    store: dict[str, bytes],
    pb_files: list[str],
) -> dict[str, tuple[dict[str, int], dict[str, Any]]]:
    monkeypatch.setattr(compaction, "list_pb_files", lambda *_a, **_k: pb_files)
    monkeypatch.setattr(GCSResource, "get_client", lambda _self: _FakeClient(store))
    gcs = GCSResource(protobuf_bucket="pb-bucket", parquet_bucket="pq-bucket")
    context = dg.build_asset_context(partition_key=PARTITION)
    return compaction.compact_feed_tables(
        context, gcs, "trip_updates", compaction.TRIP_UPDATES_TABLES
    )


def test_multi_table_pass_writes_each_populated_table_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One parse pass over mixed-entity trip_updates files must upload one
    parquet per populated table under that table's own prefix, with shared
    files_processed/files_failed and per-table records_written."""
    store = {
        "f1.pb": _mixed_tu_feed_bytes(),
        "f2.pb": _tu_only_feed_bytes("tu-2"),
    }
    results = _run_tables(monkeypatch, store, ["f1.pb", "f2.pb"])

    assert results["trip_updates"][0] == {
        "files_processed": 2,
        "records_written": 2,
        "files_failed": 0,
    }
    for table in ("trip_modifications", "shapes", "stops"):
        assert results[table][0] == {
            "files_processed": 2,
            "records_written": 1,
            "files_failed": 0,
        }

    uploaded = sorted(k for k in store if k.endswith("data.parquet"))
    assert uploaded == [
        f"{table}/date=2026-07-01/base64url={compaction.encode_base64url('https://example.com/feed')}/data.parquet"
        for table in sorted(("trip_updates", "trip_modifications", "shapes", "stops"))
    ]
    tu_table = pq.read_table(
        io.BytesIO(store[[k for k in uploaded if k.startswith("trip_updates/")][0]])
    )
    assert tu_table.column("entity_id").to_pylist() == ["tu-1", "tu-2"]


def test_multi_table_zero_record_tables_upload_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 29-of-31 case: a TU feed with no shape/stop/trip_modifications
    entities uploads ONLY the trip_updates parquet; the other outputs
    report zero records and carry no output_path."""
    store = {"f1.pb": _tu_only_feed_bytes("tu-1")}
    results = _run_tables(monkeypatch, store, ["f1.pb"])

    uploaded = [k for k in store if k.endswith("data.parquet")]
    assert len(uploaded) == 1
    assert uploaded[0].startswith("trip_updates/")
    for table in ("trip_modifications", "shapes", "stops"):
        value, metadata = results[table]
        assert value["records_written"] == 0
        assert "output_path" not in metadata


def test_parse_failure_skips_file_for_every_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file that fails parsing is skipped consistently across all
    outputs: files_failed reported on every table, no partial rows from
    the bad file anywhere."""
    store = {
        "f1.pb": _mixed_tu_feed_bytes(),
        "bad.pb": b"\x08",  # truncated varint -> DecodeError
    }
    results = _run_tables(monkeypatch, store, ["f1.pb", "bad.pb"])

    for table in ("trip_updates", "trip_modifications", "shapes", "stops"):
        value, _metadata = results[table]
        assert value["files_failed"] == 1, table
        assert value["files_processed"] == 2, table
        assert value["records_written"] == 1, table


def test_multi_table_conversion_failure_names_file_and_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conversion failure in ANY table's records fails the whole
    partition, naming the offending file and table, and nothing is
    uploaded for any table."""
    store = {"f1.pb": _mixed_tu_feed_bytes()}

    def bad_extractor(_feed: Any, source_file: str, feed_url: str, _ts: Any) -> Any:
        yield {
            "source_file": source_file,
            "feed_url": feed_url,
            "entity_id": "x",
            "latitude": "not-a-float",
        }

    specs = (
        compaction.TableSpec(
            "vehicle_positions", VEHICLE_POSITIONS_SCHEMA, compaction.extract_vehicle_positions
        ),
        compaction.TableSpec("poison_table", VEHICLE_POSITIONS_SCHEMA, bad_extractor),
    )
    monkeypatch.setattr(compaction, "list_pb_files", lambda *_a, **_k: ["f1.pb"])
    monkeypatch.setattr(GCSResource, "get_client", lambda _self: _FakeClient(store))
    gcs = GCSResource(protobuf_bucket="pb-bucket", parquet_bucket="pq-bucket")
    context = dg.build_asset_context(partition_key=PARTITION)

    with pytest.raises(dg.Failure, match=r"f1\.pb \(poison_table\)"):
        compaction.compact_feed_tables(context, gcs, "trip_updates", specs)

    assert not [k for k in store if k.endswith("data.parquet")]


def test_trip_updates_tables_asset_emits_all_four_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The @multi_asset wiring end-to-end: materializing the trip_updates
    partition yields all four outputs with per-table metadata."""
    store = {"f1.pb": _mixed_tu_feed_bytes()}
    monkeypatch.setattr(compaction, "list_pb_files", lambda *_a, **_k: ["f1.pb"])
    monkeypatch.setattr(GCSResource, "get_client", lambda _self: _FakeClient(store))

    with dg.instance_for_test() as instance:
        instance.add_dynamic_partitions("trip_updates_feeds", ["example.com/feed"])
        result = dg.materialize(
            [compaction.trip_updates_tables],
            partition_key=PARTITION,
            resources={"gcs": GCSResource(protobuf_bucket="pb-bucket", parquet_bucket="pq-bucket")},
            instance=instance,
        )

    assert result.success
    for output_name in (
        "trip_updates_parquet",
        "trip_modifications_parquet",
        "shapes_parquet",
        "stops_parquet",
    ):
        value = result.output_for_node("trip_updates_tables", output_name)
        assert value["files_processed"] == 1, output_name
        assert value["records_written"] == 1, output_name
    assert len([k for k in store if k.endswith("data.parquet")]) == 4


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
