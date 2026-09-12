from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.applicant_name_lookup import (
    CN_APPLICANT_NAME_LOOKUP_COLUMNS,
    CN_APPLICANT_NAME_LOOKUP_TABLE,
    cn_applicant_name_lookup_row,
)

DEFAULT_BATCH_SIZE = 5_000
MAX_BATCH_SIZE = 20_000
READ_SETTINGS = {"max_threads": 1, "max_rows_to_read": 50_000_000, "read_overflow_mode": "throw"}
SOURCE_COLUMNS = (
    "application_number",
    "role",
    "relation_key",
    "entity_id",
    "raw_name",
    "normalized_name",
    "source_row_hash",
    "record_hash",
    "source_rank",
    "ingested_at",
)


@dataclass(frozen=True, slots=True)
class CNApplicantNameLookupBackfillCursor:
    after_application_number: str = ""
    after_role: str = ""
    after_relation_key: str = ""
    emitted: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "after_application_number": self.after_application_number,
            "after_role": self.after_role,
            "after_relation_key": self.after_relation_key,
            "emitted": self.emitted,
        }


def _sql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _assert_epoch(expected: str | None, getter: Callable[[], str] | None) -> None:
    if expected is None:
        return
    if getter is None:
        raise ValueError("serving_epoch_getter is required with expected_epoch")
    if getter() != expected:
        raise RuntimeError("CN Applicant name lookup source serving epoch changed during backfill")


def _page_sql(cursor: CNApplicantNameLookupBackfillCursor, limit: int) -> str:
    cursor_sql = ""
    if cursor.after_application_number:
        cursor_sql = (
            " AND (application_number, role, relation_key) > "
            f"({_sql_text(cursor.after_application_number)}, {_sql_text(cursor.after_role)}, "
            f"{_sql_text(cursor.after_relation_key)})"
        )
    return f"""
        SELECT {", ".join(SOURCE_COLUMNS)}
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0 AND is_current = 1
          AND role IN ('OWNER', 'CO_OWNER') AND entity_id IS NOT NULL
          AND normalized_name != ''{cursor_sql}
        ORDER BY application_number, role, relation_key
        LIMIT {limit}
    """


def backfill_cn_applicant_name_lookup(
    *,
    client: Any,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rows: int | None = None,
    cursor: CNApplicantNameLookupBackfillCursor | None = None,
    expected_epoch: str | None = None,
    serving_epoch_getter: Callable[[], str] | None = None,
    checkpoint: Callable[[CNApplicantNameLookupBackfillCursor], None] | None = None,
) -> CNApplicantNameLookupBackfillCursor:
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be positive when provided")
    state = cursor or CNApplicantNameLookupBackfillCursor()
    _assert_epoch(expected_epoch, serving_epoch_getter)
    while True:
        remaining = None if max_rows is None else max_rows - state.emitted
        if remaining is not None and remaining <= 0:
            break
        limit = batch_size if remaining is None else min(batch_size, remaining)
        _assert_epoch(expected_epoch, serving_epoch_getter)
        rows = list(client.query(_page_sql(state, limit), settings=READ_SETTINGS).result_rows)
        if not rows:
            break
        output = []
        for row in rows:
            lookup = cn_applicant_name_lookup_row(dict(zip(SOURCE_COLUMNS, row, strict=True)))
            if lookup is None:
                raise RuntimeError("eligible CN Applicant row did not produce a lookup binding")
            output.append(lookup)
        client.insert(
            CN_APPLICANT_NAME_LOOKUP_TABLE, output, column_names=CN_APPLICANT_NAME_LOOKUP_COLUMNS
        )
        last = dict(zip(SOURCE_COLUMNS, rows[-1], strict=True))
        next_state = CNApplicantNameLookupBackfillCursor(
            str(last["application_number"]),
            str(last["role"]),
            str(last["relation_key"]),
            state.emitted + len(output),
        )
        _assert_epoch(expected_epoch, serving_epoch_getter)
        if checkpoint is not None:
            checkpoint(next_state)
        state = next_state
        if len(rows) < limit:
            break
    return state
