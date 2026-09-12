from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.applicant_name_lookup import (
    US_APPLICANT_NAME_LOOKUP_TABLE,
    US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS,
    us_applicant_name_lookup_row,
)
from app.us.publisher import APPLICANT_INDEX_COLUMNS

DEFAULT_BATCH_SIZE = 5_000
MAX_BATCH_SIZE = 20_000
READ_SETTINGS = {
    "max_threads": 1,
    "max_rows_to_read": 50_000_000,
    "read_overflow_mode": "throw",
}


@dataclass(frozen=True, slots=True)
class ApplicantNameLookupBackfillCursor:
    after_candidate_key: str = ""
    after_serial: str = ""
    after_owner_key: str = ""
    emitted: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "after_candidate_key": self.after_candidate_key,
            "after_serial": self.after_serial,
            "after_owner_key": self.after_owner_key,
            "emitted": self.emitted,
        }


def _sql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _assert_epoch(expected_epoch: str | None, getter: Callable[[], str] | None) -> None:
    if expected_epoch is None:
        return
    if getter is None:
        raise ValueError("serving_epoch_getter is required with expected_epoch")
    if getter() != expected_epoch:
        raise RuntimeError("US Applicant name lookup source serving epoch changed during backfill")


def _page_sql(cursor: ApplicantNameLookupBackfillCursor, limit: int) -> str:
    cursor_sql = ""
    if cursor.after_candidate_key:
        cursor_sql = (
            " AND (candidate_key, serial_number, owner_key) > "
            f"({_sql_text(cursor.after_candidate_key)}, "
            f"{_sql_text(cursor.after_serial)}, {_sql_text(cursor.after_owner_key)})"
        )
    return f"""
        SELECT {", ".join(APPLICANT_INDEX_COLUMNS)}
        FROM markorbit_facts.us_applicant_candidate_current FINAL
        WHERE is_deleted = 0{cursor_sql}
        ORDER BY candidate_key, serial_number, owner_key
        LIMIT {limit}
    """


def backfill_us_applicant_name_lookup(
    *,
    client: Any,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rows: int | None = None,
    cursor: ApplicantNameLookupBackfillCursor | None = None,
    expected_epoch: str | None = None,
    serving_epoch_getter: Callable[[], str] | None = None,
    checkpoint: Callable[[ApplicantNameLookupBackfillCursor], None] | None = None,
) -> ApplicantNameLookupBackfillCursor:
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be positive when provided")
    state = cursor or ApplicantNameLookupBackfillCursor()
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

        lookup_rows = []
        for row in rows:
            source = dict(zip(APPLICANT_INDEX_COLUMNS, row, strict=True))
            lookup_row = us_applicant_name_lookup_row(source)
            del lookup_row[7]
            lookup_rows.append(lookup_row)
        client.insert(
            US_APPLICANT_NAME_LOOKUP_TABLE,
            lookup_rows,
            column_names=list(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS),
        )

        last = dict(zip(APPLICANT_INDEX_COLUMNS, rows[-1], strict=True))
        next_state = ApplicantNameLookupBackfillCursor(
            after_candidate_key=str(last["candidate_key"]),
            after_serial=str(last["serial_number"]),
            after_owner_key=str(last["owner_key"]),
            emitted=state.emitted + len(lookup_rows),
        )
        _assert_epoch(expected_epoch, serving_epoch_getter)
        if checkpoint is not None:
            checkpoint(next_state)
        state = next_state
        if len(rows) < limit:
            break

    return state
