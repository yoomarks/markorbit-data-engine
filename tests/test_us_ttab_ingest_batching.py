from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import uuid

import pytest

import app.us_ttab.ingest as ingest
from app.us_ttab.model import (
    TTABDocketRecord,
    TTABPartyRecord,
    TTABProceedingBundle,
    TTABProceedingRecord,
    TTABPropertyRecord,
)


class _Result:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _FakeClickHouse:
    def __init__(
        self,
        collision_query: int | None = None,
        rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.queries: list[str] = []
        self.collision_query = collision_query
        self.rows = rows or []

    def query(self, sql: str) -> _Result:
        self.queries.append(sql)
        if self.collision_query == len(self.queries):
            return _Result(
                self.rows
                or [
                    (
                        "90000500",
                        "22222222-2222-4222-8222-222222222222",
                        "P",
                        "different",
                    )
                ]
            )
        return _Result(self.rows if self.collision_query is None else [])


class _FakePublisher:
    instances: list["_FakePublisher"] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.added = 0
        self.instances.append(self)

    def add(self, _bundle: object, _source_file: str) -> None:
        self.added += 1

    def close(self) -> dict[str, int]:
        return {table: 0 for table in ingest.TABLE_COLUMNS}


def _bundle(number: int, *, party_name: str = "Example Party") -> TTABProceedingBundle:
    proceeding_number = f"{number:08d}"
    return TTABProceedingBundle(
        proceeding=TTABProceedingRecord(
            proceeding_number=proceeding_number,
            proceeding_type_code="OPP",
        ),
        parties=(
            TTABPartyRecord(
                proceeding_number=proceeding_number,
                side="ROLE_P",
                ordinal=1,
                party_name=party_name,
            ),
        ),
        properties=(
            TTABPropertyRecord(
                proceeding_number=proceeding_number,
                party_side="ROLE_P",
                party_ordinal=1,
                ordinal=1,
                serial_number=proceeding_number,
            ),
        ),
        docket_entries=(
            TTABDocketRecord(
                proceeding_number=proceeding_number,
                ordinal=1,
                entry_number="1",
                history_text="FILED",
            ),
        ),
    )


def _signature_rows(
    bundle: TTABProceedingBundle, package_id: uuid.UUID
) -> list[tuple[object, ...]]:
    number = bundle.proceeding.proceeding_number
    return [
        (number, str(package_id), family, record_hash)
        for family, record_hash in ingest._bundle_signature(bundle)
    ]


def _meta() -> dict[str, object]:
    return {
        "jurisdiction": "US_TTAB",
        "partition_value": "2026-09-03T17:01:00+00:00",
        "sha256": "a" * 64,
        "package_kind": "TTAB_BULK_HISTORICAL_XML",
        "source_period_start": date(2026, 9, 2),
        "source_period_end": date(2026, 9, 2),
        "source_rank": 1,
    }


def _patch_ingest(monkeypatch: pytest.MonkeyPatch, fake_ch: _FakeClickHouse, count: int) -> None:
    monkeypatch.setattr(ingest, "ensure_ttab_schema", lambda: None)
    monkeypatch.setattr(ingest, "clickhouse_client", lambda: fake_ch)
    monkeypatch.setattr(ingest, "TTABBatchPublisher", _FakePublisher)
    monkeypatch.setattr(ingest, "sha256_file", lambda _path: "a" * 64)
    monkeypatch.setattr(ingest, "create_job_run", lambda **_kwargs: "job-1")
    monkeypatch.setattr(ingest, "finish_job_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest, "update_package_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest, "cleanup_ttab_package_outputs", lambda _package_id: None)
    monkeypatch.setattr(ingest, "_archive", lambda path, _raw_root: path)
    monkeypatch.setattr(ingest, "get_package", lambda _package_id: _meta())
    monkeypatch.setattr(ingest, "_historical_batch_package_ids", lambda *_args: set())
    monkeypatch.setattr(
        ingest,
        "_iter_source",
        lambda _path: (("bulk.xml", _bundle(90000000 + i)) for i in range(count)),
    )
    _FakePublisher.instances.clear()


def test_ingest_batches_snapshot_slot_queries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_ch = _FakeClickHouse()
    _patch_ingest(monkeypatch, fake_ch, 1201)
    source = tmp_path / "ttab.zip"
    source.write_bytes(b"x")

    result = ingest.ingest_ttab_package(
        "11111111-1111-4111-8111-111111111111",
        source,
        tmp_path,
    )

    assert result["proceeding_count"] == 1201
    assert result["published_proceeding_count"] == 1201
    assert result["historical_batch_duplicate_count"] == 0
    assert len(fake_ch.queries) == 3
    assert _FakePublisher.instances[0].added == 1201


def test_non_batch_snapshot_collision_remains_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(90000500)
    fake_ch = _FakeClickHouse(
        rows=[
            (
                bundle.proceeding.proceeding_number,
                "22222222-2222-4222-8222-222222222222",
                "P",
                "different",
            )
        ]
    )
    monkeypatch.setattr(ingest, "clickhouse_client", lambda: fake_ch)

    with pytest.raises(RuntimeError, match="outside one identical historical split batch"):
        ingest._publishable_snapshot_batch(
            [("bulk.xml", bundle)],
            datetime(2026, 9, 3, 17, 1, tzinfo=timezone.utc),
            uuid.UUID("11111111-1111-4111-8111-111111111111"),
            _meta(),
            set(),
        )

    assert len(fake_ch.queries) == 1
    assert bundle.proceeding.proceeding_number in fake_ch.queries[0]


def test_identical_historical_split_overlap_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(90000500)
    existing = uuid.UUID("22222222-2222-4222-8222-222222222222")
    fake_ch = _FakeClickHouse(rows=_signature_rows(bundle, existing))
    monkeypatch.setattr(ingest, "clickhouse_client", lambda: fake_ch)

    publishable, skipped = ingest._publishable_snapshot_batch(
        [("part-2.xml", bundle)],
        datetime(2026, 9, 3, 17, 1, tzinfo=timezone.utc),
        uuid.UUID("11111111-1111-4111-8111-111111111111"),
        _meta(),
        {existing},
    )

    assert publishable == []
    assert skipped == 1
    assert "source_package_id IN" in fake_ch.queries[0]


def test_historical_split_overlap_with_child_difference_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_bundle = _bundle(90000500, party_name="Original Party")
    incoming_bundle = _bundle(90000500, party_name="Changed Party")
    existing = uuid.UUID("22222222-2222-4222-8222-222222222222")
    fake_ch = _FakeClickHouse(rows=_signature_rows(existing_bundle, existing))
    monkeypatch.setattr(ingest, "clickhouse_client", lambda: fake_ch)

    with pytest.raises(RuntimeError, match="historical split batch contains conflicting content"):
        ingest._publishable_snapshot_batch(
            [("part-2.xml", incoming_bundle)],
            datetime(2026, 9, 3, 17, 1, tzinfo=timezone.utc),
            uuid.UUID("11111111-1111-4111-8111-111111111111"),
            _meta(),
            {existing},
        )


def test_collision_batch_is_not_published(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_ch = _FakeClickHouse(collision_query=2)
    _patch_ingest(monkeypatch, fake_ch, 1000)
    source = tmp_path / "ttab.zip"
    source.write_bytes(b"x")

    with pytest.raises(RuntimeError, match="outside one identical historical split batch"):
        ingest.ingest_ttab_package(
            "11111111-1111-4111-8111-111111111111",
            source,
            tmp_path,
        )

    assert len(fake_ch.queries) == 2
    assert _FakePublisher.instances[0].added == 500
