from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

import app.us_ttab.ingest as ingest


class _Result:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(self, collision_query: int | None = None) -> None:
        self.queries: list[str] = []
        self.collision_query = collision_query

    def query(self, sql: str) -> _Result:
        self.queries.append(sql)
        if self.collision_query == len(self.queries):
            return _Result([("90000501",)])
        return _Result([])


class _FakePublisher:
    instances: list["_FakePublisher"] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.added = 0
        self.instances.append(self)

    def add(self, _bundle: object, _source_file: str) -> None:
        self.added += 1

    def close(self) -> dict[str, int]:
        return {table: 0 for table in ingest.TABLE_COLUMNS}


def _bundle(number: int) -> SimpleNamespace:
    proceeding = SimpleNamespace(
        proceeding_number=f"{number:08d}",
        proceeding_type="",
        proceeding_type_code="OPP",
    )
    return SimpleNamespace(
        proceeding=proceeding,
        properties=(),
        docket_entries=(),
    )


def _patch_ingest(monkeypatch: pytest.MonkeyPatch, fake_ch: _FakeClickHouse, count: int) -> None:
    package_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(ingest, "ensure_ttab_schema", lambda: None)
    monkeypatch.setattr(ingest, "clickhouse_client", lambda: fake_ch)
    monkeypatch.setattr(ingest, "TTABBatchPublisher", _FakePublisher)
    monkeypatch.setattr(ingest, "sha256_file", lambda _path: "a" * 64)
    monkeypatch.setattr(ingest, "create_job_run", lambda **_kwargs: "job-1")
    monkeypatch.setattr(ingest, "finish_job_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest, "update_package_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest, "cleanup_ttab_package_outputs", lambda _package_id: None)
    monkeypatch.setattr(ingest, "_archive", lambda path, _raw_root: path)
    monkeypatch.setattr(
        ingest,
        "get_package",
        lambda _package_id: {
            "jurisdiction": "US_TTAB",
            "partition_value": "2026-09-03T17:01:00+00:00",
            "sha256": "a" * 64,
            "package_kind": "TTAB_BULK_HISTORICAL_XML",
            "source_rank": 1,
        },
    )
    monkeypatch.setattr(
        ingest,
        "_iter_source",
        lambda _path: (("bulk.xml", _bundle(90000000 + i)) for i in range(count)),
    )
    _FakePublisher.instances.clear()
    assert str(package_id)


def test_ingest_batches_snapshot_slot_queries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_ch = _FakeClickHouse()
    _patch_ingest(monkeypatch, fake_ch, 1201)
    source = tmp_path / "ttab.zip"
    source.write_bytes(b"x")

    result = ingest.ingest_ttab_package(
        "11111111-1111-1111-1111-111111111111",
        source,
        tmp_path,
    )

    assert result["proceeding_count"] == 1201
    assert len(fake_ch.queries) == 3
    assert _FakePublisher.instances[0].added == 1201


def test_snapshot_slot_batch_collision_remains_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ch = _FakeClickHouse(collision_query=1)
    monkeypatch.setattr(ingest, "clickhouse_client", lambda: fake_ch)

    with pytest.raises(RuntimeError, match="same proceeding and millisecond"):
        ingest._assert_snapshot_slots_available(
            ["90000500", "90000501"],
            datetime(2026, 9, 3, 17, 1, tzinfo=timezone.utc),
            uuid.UUID("11111111-1111-1111-1111-111111111111"),
        )

    assert len(fake_ch.queries) == 1
    assert "90000500" in fake_ch.queries[0]
    assert "90000501" in fake_ch.queries[0]


def test_collision_batch_is_not_published(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_ch = _FakeClickHouse(collision_query=2)
    _patch_ingest(monkeypatch, fake_ch, 1000)
    source = tmp_path / "ttab.zip"
    source.write_bytes(b"x")

    with pytest.raises(RuntimeError, match="same proceeding and millisecond"):
        ingest.ingest_ttab_package(
            "11111111-1111-1111-1111-111111111111",
            source,
            tmp_path,
        )

    assert len(fake_ch.queries) == 2
    assert _FakePublisher.instances[0].added == 500
