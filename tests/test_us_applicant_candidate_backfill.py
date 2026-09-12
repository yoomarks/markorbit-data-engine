from __future__ import annotations

import pytest

from app.us.applicant_candidate_backfill import (
    ApplicantIndexBackfillCursor,
    backfill_us_applicant_candidate_index,
)
from app.us.applicant_candidate_index import APPLICANT_INDEX_TABLE
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
        self.inserts.append((table, [list(row) for row in rows], list(column_names)))


def _owner_row(serial: str, owner_key: str, *, address: str = "1 Main") -> tuple[object, ...]:
    values = {column: "" for column in OWNER_COLUMNS}
    values.update(
        owner_key=owner_key,
        serial_number=serial,
        entry_number=1,
        party_type="10",
        legal_entity_type_code="16",
        party_name="Acme LLC",
        party_name_norm="ACME LLC",
        address_1=address,
        source_row_hash="1" * 64,
        last_source_package_id="11111111-1111-1111-1111-111111111111",
        record_hash="2" * 64,
        source_rank=100,
        is_deleted=0,
    )
    return tuple(values[column] for column in OWNER_COLUMNS)


def test_backfill_checkpoints_native_keyset_and_is_visible_idempotent():
    row1 = _owner_row("10000001", "a" * 64)
    row2 = _owner_row("10000002", "b" * 64)
    row3 = _owner_row("10000003", "c" * 64)
    client = FakeClient([[row1, row2], [row3]])
    checkpoints = []

    final = backfill_us_applicant_candidate_index(
        client=client,
        batch_size=2,
        expected_epoch="epoch-1",
        serving_epoch_getter=lambda: "epoch-1",
        checkpoint=checkpoints.append,
    )

    assert final == ApplicantIndexBackfillCursor("10000003", "c" * 64, 3)
    assert checkpoints == [
        ApplicantIndexBackfillCursor("10000002", "b" * 64, 2),
        ApplicantIndexBackfillCursor("10000003", "c" * 64, 3),
    ]
    assert all(insert[0] == APPLICANT_INDEX_TABLE for insert in client.inserts)
    assert all(insert[2] == APPLICANT_INDEX_COLUMNS for insert in client.inserts)
    assert "(serial_number, owner_key) >" in client.queries[1][0]
    assert all(query[1]["max_threads"] == 1 for query in client.queries)


def test_backfill_epoch_drift_fails_before_checkpoint():
    row = _owner_row("10000001", "a" * 64)
    client = FakeClient([[row]])
    observed = iter(["epoch-1", "epoch-1", "epoch-2"])
    checkpoints = []
    with pytest.raises(RuntimeError, match="serving epoch changed"):
        backfill_us_applicant_candidate_index(
            client=client,
            batch_size=1,
            expected_epoch="epoch-1",
            serving_epoch_getter=lambda: next(observed),
            checkpoint=checkpoints.append,
        )

    assert len(client.inserts) == 1
    assert checkpoints == []


def test_resume_cursor_starts_after_durable_binding():
    row = _owner_row("10000003", "c" * 64)
    client = FakeClient([[row]])
    final = backfill_us_applicant_candidate_index(
        client=client,
        batch_size=10,
        cursor=ApplicantIndexBackfillCursor("10000002", "b" * 64, 2),
    )
    assert final.emitted == 3
    sql = client.queries[0][0]
    assert "'10000002'" in sql
    assert "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'" in sql


def test_cooperative_stop_happens_before_next_page():
    client = FakeClient([])
    with pytest.raises(InterruptedError, match="stop requested"):
        backfill_us_applicant_candidate_index(client=client, stop_requested=lambda: True)
    assert client.queries == []
