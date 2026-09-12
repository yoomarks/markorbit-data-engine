from __future__ import annotations

import uuid
import pytest

from app.applicant_name_lookup import CN_APPLICANT_NAME_LOOKUP_TABLE
from app.cn.applicant_name_lookup_backfill import (
    CNApplicantNameLookupBackfillCursor,
    backfill_cn_applicant_name_lookup,
)


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self, pages):
        self.pages, self.queries, self.inserts = list(pages), [], []

    def query(self, sql, settings=None):
        self.queries.append(sql)
        return Result(self.pages.pop(0) if self.pages else [])

    def insert(self, table, rows, column_names):
        self.inserts.append((table, rows, column_names))


def _row(application, role, relation):
    return (
        application,
        role,
        relation,
        uuid.UUID("8f50ed78-7666-4cab-8a0c-f91c89de0ca7"),
        "北京示例（有限）公司",
        "北京示例有限公司",
        "a" * 64,
        "b" * 64,
        10,
        "2026-09-13",
    )


def test_backfill_pages_native_key_and_checkpoints_after_write():
    first, second = _row("CN1", "OWNER", "a" * 64), _row("CN2", "CO_OWNER", "b" * 64)
    client, checkpoints = Client([[first], [second]]), []
    result = backfill_cn_applicant_name_lookup(
        client=client, batch_size=1, checkpoint=checkpoints.append
    )
    assert result == CNApplicantNameLookupBackfillCursor("CN2", "CO_OWNER", "b" * 64, 2)
    assert len(checkpoints) == 2
    assert all(write[0] == CN_APPLICANT_NAME_LOOKUP_TABLE for write in client.inserts)
    assert "role IN ('OWNER', 'CO_OWNER')" in client.queries[0]
    assert "entity_id IS NOT NULL" in client.queries[0]
    assert "(application_number, role, relation_key) >" in client.queries[1]


def test_epoch_drift_fails_before_checkpoint():
    client, checkpoints = Client([[_row("CN1", "OWNER", "a" * 64)]]), []
    epochs = iter(["one", "one", "two"])
    with pytest.raises(RuntimeError, match="serving epoch changed"):
        backfill_cn_applicant_name_lookup(
            client=client,
            expected_epoch="one",
            serving_epoch_getter=lambda: next(epochs),
            checkpoint=checkpoints.append,
        )
    assert checkpoints == []
    assert len(client.inserts) == 1
