from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.us.applicant_candidate_index import APPLICANT_INDEX_TABLE, applicant_index_row
from app.us.publisher import APPLICANT_INDEX_COLUMNS, OWNER_COLUMNS

DEFAULT_BATCH_SIZE = 5_000
MAX_BATCH_SIZE = 20_000
READ_SETTINGS = {
    "max_threads": 1,
    "max_rows_to_read": 50_000_000,
    "read_overflow_mode": "throw",
}


@dataclass(frozen=True, slots=True)
class ApplicantIndexBackfillCursor:
    after_serial: str = ""
    after_owner_key: str = ""
    emitted: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "after_serial": self.after_serial,
            "after_owner_key": self.after_owner_key,
            "emitted": self.emitted,
        }


def _sql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _assert_epoch(
    *,
    expected_epoch: str | None,
    serving_epoch_getter: Callable[[], str] | None,
) -> None:
    if expected_epoch is None:
        return
    if serving_epoch_getter is None:
        raise ValueError("serving_epoch_getter is required with expected_epoch")
    observed = serving_epoch_getter()
    if observed != expected_epoch:
        raise RuntimeError(
            "US Applicant index source serving epoch changed during backfill; "
            "partial output is not resumable against a different epoch"
        )


def _page_sql(cursor: ApplicantIndexBackfillCursor, limit: int) -> str:
    columns_sql = ", ".join(OWNER_COLUMNS)
    cursor_sql = ""
    if cursor.after_serial:
        cursor_sql = (
            " AND (serial_number, owner_key) > "
            f"({_sql_text(cursor.after_serial)}, {_sql_text(cursor.after_owner_key)})"
        )
    return f"""
        SELECT {columns_sql}
        FROM markorbit_facts.us_owner_current FINAL
        WHERE is_deleted = 0{cursor_sql}
        ORDER BY serial_number, owner_key
        LIMIT {limit}
    """


def backfill_us_applicant_candidate_index(
    *,
    client: Any,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rows: int | None = None,
    cursor: ApplicantIndexBackfillCursor | None = None,
    expected_epoch: str | None = None,
    serving_epoch_getter: Callable[[], str] | None = None,
    checkpoint: Callable[[ApplicantIndexBackfillCursor], None] | None = None,
) -> ApplicantIndexBackfillCursor:
    """Backfill the derived index in resumable native-key order.

    Replaying a checkpointed page is visible-idempotent because the target uses
    the owner row's source_rank under a ReplacingMergeTree key.
    """
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be positive when provided")
    state = cursor or ApplicantIndexBackfillCursor()
    _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)

    while True:
        remaining = None if max_rows is None else max_rows - state.emitted
        if remaining is not None and remaining <= 0:
            break
        limit = batch_size if remaining is None else min(batch_size, remaining)
        _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
        result = client.query(_page_sql(state, limit), settings=READ_SETTINGS)
        rows = list(result.result_rows)
        if not rows:
            break

        index_rows = [applicant_index_row(row, OWNER_COLUMNS) for row in rows]
        client.insert(
            APPLICANT_INDEX_TABLE,
            index_rows,
            column_names=APPLICANT_INDEX_COLUMNS,
        )
        serial_index = OWNER_COLUMNS.index("serial_number")
        owner_index = OWNER_COLUMNS.index("owner_key")
        next_state = ApplicantIndexBackfillCursor(
            after_serial=str(rows[-1][serial_index]),
            after_owner_key=str(rows[-1][owner_index]),
            emitted=state.emitted + len(index_rows),
        )
        _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
        if checkpoint is not None:
            checkpoint(next_state)
        state = next_state
        if len(rows) < limit:
            break

    return state
