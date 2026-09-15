from __future__ import annotations

import pytest

from app.applicant_name_lookup import (
    US_APPLICANT_NAME_LOOKUP_TABLE,
    US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS,
)
from app.us.applicant_name_lookup_backfill import (
    ApplicantNameLookupBackfillCursor,
    backfill_us_applicant_name_lookup,
    reconcile_us_applicant_name_lookup,
)
from app.us.applicant_candidate_index import applicant_candidate_key
from app.us.publisher import APPLICANT_INDEX_COLUMNS, OWNER_COLUMNS


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.queries = []
        self.inserts = []

    def query(self, sql, settings=None):
        self.queries.append((sql, settings))
        return Result(self.pages.pop(0) if self.pages else [])

    def insert(self, table, rows, column_names):
        self.inserts.append((table, rows, column_names))


def _candidate_row(serial: str, owner_key: str) -> tuple[object, ...]:
    owner = {column: "" for column in OWNER_COLUMNS}
    owner.update(
        owner_key=owner_key,
        serial_number=serial,
        party_name_norm="Acme LLC",
        source_row_hash="1" * 64,
        record_hash="2" * 64,
        source_rank=100,
        is_deleted=0,
    )
    return (applicant_candidate_key(owner), *(owner[column] for column in OWNER_COLUMNS))


def test_backfill_uses_candidate_native_key_and_checkpoints_after_insert():
    first = _candidate_row("10000001", "a" * 64)
    second = _candidate_row("10000002", "b" * 64)
    client = FakeClient([[first], [second]])
    checkpoints = []

    final = backfill_us_applicant_name_lookup(
        client=client, batch_size=1, checkpoint=checkpoints.append
    )

    assert final == ApplicantNameLookupBackfillCursor(second[0], "10000002", "b" * 64, 2)
    assert len(checkpoints) == 2
    assert all(item[0] == US_APPLICANT_NAME_LOOKUP_TABLE for item in client.inserts)
    assert all(item[2] == list(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS) for item in client.inserts)
    assert "(candidate_key, serial_number, owner_key) >" in client.queries[1][0]
    assert client.inserts[0][1][0][0] == "acme llc"
    assert all(query[1]["max_rows_to_read"] == 100_000_000 for query in client.queries)
    assert all(query[1]["read_overflow_mode"] == "throw" for query in client.queries)


def test_epoch_drift_prevents_checkpoint():
    client = FakeClient([[_candidate_row("10000001", "a" * 64)]])
    observed = iter(["epoch-1", "epoch-1", "epoch-2"])
    checkpoints = []

    with pytest.raises(RuntimeError, match="serving epoch changed"):
        backfill_us_applicant_name_lookup(
            client=client,
            expected_epoch="epoch-1",
            serving_epoch_getter=lambda: next(observed),
            checkpoint=checkpoints.append,
        )

    assert len(client.inserts) == 1
    assert checkpoints == []


def test_cooperative_stop_happens_before_next_page():
    client = FakeClient([])
    with pytest.raises(InterruptedError, match="stop requested"):
        backfill_us_applicant_name_lookup(client=client, stop_requested=lambda: True)
    assert client.queries == []


def _candidate_source(row):
    return dict(zip(APPLICANT_INDEX_COLUMNS, row, strict=True))


def _candidate_binding(row):
    source = _candidate_source(row)
    return (
        source["candidate_key"],
        source["serial_number"],
        source["owner_key"],
        source["record_hash"],
        source["source_rank"],
    )


def test_reconcile_inserts_only_bounded_missing_bindings():
    candidate = _candidate_row("10000001", "a" * 64)
    client = FakeClient([[ _candidate_binding(candidate) ], [candidate], [], []])

    reconciled = reconcile_us_applicant_name_lookup(client=client, max_rows=2)

    assert reconciled == 1
    assert "LEFT JOIN" in client.queries[0][0]
    assert "SELECT source.candidate_key, source.serial_number, source.owner_key" in client.queries[0][0]
    assert "source.address_1" not in client.queries[0][0]
    assert "target.record_hash = source.record_hash" in client.queries[0][0]
    assert "LIMIT 3" in client.queries[0][0]
    assert client.inserts[0][0] == US_APPLICANT_NAME_LOOKUP_TABLE
    assert client.queries[0][1]["max_rows_to_read"] == 150_000_000
    assert client.queries[0][1]["join_algorithm"] == "full_sorting_merge"
    assert client.queries[0][1]["max_bytes_before_external_sort"] == 268_435_456


def test_reconcile_fails_closed_above_bound():
    first = _candidate_row("10000001", "a" * 64)
    second = _candidate_row("10000002", "b" * 64)
    client = FakeClient([[ _candidate_binding(first), _candidate_binding(second) ]])
    with pytest.raises(RuntimeError, match="exceeds bounded limit 1"):
        reconcile_us_applicant_name_lookup(client=client, max_rows=1)
    assert client.inserts == []


def test_reconcile_tombstones_superseded_lookup_identity():
    candidate = _candidate_row("10000001", "a" * 64)
    source = _candidate_source(candidate)
    stale = (
        "old name",
        "f" * 64,
        100,
        source["candidate_key"],
        source["serial_number"],
        source["owner_key"],
        source["record_hash"],
        source["source_rank"],
    )
    client = FakeClient(
        [[_candidate_binding(candidate)], [candidate], [stale], [], [], [], [candidate], []]
    )

    reconcile_us_applicant_name_lookup(client=client, max_rows=2)

    assert len(client.inserts) == 2
    tombstone = client.inserts[1][1][0]
    assert tombstone[0] == "old name"
    assert tombstone[1] == "f" * 64
    assert tombstone[-1] == 1
    assert tombstone[-2] == 101
    assert client.queries[2][1]["join_algorithm"] == "full_sorting_merge"
    stale_queries = [item[0] for item in client.queries[2:6]]
    assert all("cityHash64(serial_number, owner_key) % 4" in sql for sql in stale_queries)
    assert [f"= {bucket}" in stale_queries[bucket] for bucket in range(4)] == [True] * 4


def test_reconcile_tombstones_orphan_lookup_identity_even_without_missing_rows():
    candidate = _candidate_row("10000001", "a" * 64)
    source = _candidate_source(candidate)
    orphan_full = [
        "acme llc",
        source["candidate_key"],
        source["serial_number"],
        source["owner_key"],
        source["source_row_hash"],
        source["record_hash"],
        source["source_rank"],
        0,
    ]
    orphan_compact = (
        orphan_full[0],
        orphan_full[1],
        orphan_full[2],
        orphan_full[3],
        orphan_full[6],
    )
    client = FakeClient([[], [], [], [], [], [orphan_compact], [orphan_full]])

    reconciled = reconcile_us_applicant_name_lookup(client=client, max_rows=2)

    assert reconciled == 1
    assert len(client.inserts) == 1
    tombstone = client.inserts[0][1][0]
    assert tombstone[0] == "acme llc"
    assert tombstone[1] == source["candidate_key"]
    assert tombstone[-1] == 1
    assert tombstone[-2] == 101
    assert "LEFT JOIN" in client.queries[5][0]
    assert "source.serial_number = target.serial_number" in client.queries[5][0]
