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
MAX_RECONCILE_ROWS = 500_000
READ_SETTINGS = {
    "max_threads": 1,
    "max_rows_to_read": 100_000_000,
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
    stop_requested: Callable[[], bool] | None = None,
) -> ApplicantNameLookupBackfillCursor:
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be positive when provided")
    state = cursor or ApplicantNameLookupBackfillCursor()
    _assert_epoch(expected_epoch, serving_epoch_getter)

    while True:
        if stop_requested is not None and stop_requested():
            raise InterruptedError("US Applicant name lookup backfill stop requested")
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


def reconcile_us_applicant_name_lookup(
    *,
    client: Any,
    max_rows: int = MAX_RECONCILE_ROWS,
    expected_epoch: str | None = None,
    serving_epoch_getter: Callable[[], str] | None = None,
) -> int:
    """Insert a bounded set of candidate bindings missing from NAME lookup."""
    if max_rows < 1 or max_rows > MAX_RECONCILE_ROWS:
        raise ValueError(f"max_rows must be between 1 and {MAX_RECONCILE_ROWS}")
    _assert_epoch(expected_epoch, serving_epoch_getter)
    projection = ", ".join(f"source.{column}" for column in APPLICANT_INDEX_COLUMNS)
    rows = list(
        client.query(
            f"""
            SELECT {projection}
            FROM
            (
                SELECT * FROM markorbit_facts.us_applicant_candidate_current FINAL
                WHERE is_deleted = 0
            ) AS source
            LEFT ANTI JOIN
            (
                SELECT candidate_key, serial_number, owner_key, record_hash, source_rank
                FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
              ON target.candidate_key = source.candidate_key
             AND target.serial_number = source.serial_number
             AND target.owner_key = source.owner_key
             AND target.record_hash = source.record_hash
             AND target.source_rank = source.source_rank
            ORDER BY source.candidate_key, source.serial_number, source.owner_key
            LIMIT {max_rows + 1}
            """,
            settings={
                **READ_SETTINGS,
                "max_rows_to_read": 100_000_000,
                "join_algorithm": "grace_hash",
            },
        ).result_rows
    )
    if len(rows) > max_rows:
        raise RuntimeError(
            f"US Applicant NAME lookup reconciliation exceeds bounded limit {max_rows}"
        )
    if not rows:
        return 0
    lookup_rows = []
    for row in rows:
        lookup_row = us_applicant_name_lookup_row(
            dict(zip(APPLICANT_INDEX_COLUMNS, row, strict=True))
        )
        del lookup_row[7]
        lookup_rows.append(lookup_row)
    for offset in range(0, len(lookup_rows), MAX_BATCH_SIZE):
        _assert_epoch(expected_epoch, serving_epoch_getter)
        client.insert(
            US_APPLICANT_NAME_LOOKUP_TABLE,
            lookup_rows[offset : offset + MAX_BATCH_SIZE],
            column_names=list(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS),
        )
    stale_rows = list(
        client.query(
            f"""
            SELECT target.normalized_name, target.candidate_key, target.source_rank,
                   {projection}
            FROM
            (
                SELECT * FROM markorbit_facts.us_applicant_candidate_current FINAL
                WHERE is_deleted = 0
            ) AS source
            INNER JOIN
            (
                SELECT normalized_name, candidate_key, serial_number, owner_key,
                       record_hash, source_rank
                FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
              ON target.serial_number = source.serial_number
             AND target.owner_key = source.owner_key
            WHERE target.record_hash != source.record_hash
               OR target.source_rank != source.source_rank
            ORDER BY source.candidate_key, source.serial_number, source.owner_key,
                     target.normalized_name, target.candidate_key
            LIMIT {max_rows + 1}
            """,
            settings={
                **READ_SETTINGS,
                "max_rows_to_read": 100_000_000,
                "join_algorithm": "grace_hash",
            },
        ).result_rows
    )
    if len(stale_rows) > max_rows:
        raise RuntimeError(
            f"US Applicant NAME lookup stale-binding reconciliation exceeds bounded limit {max_rows}"
        )
    tombstones = []
    for row in stale_rows:
        old_source_rank = int(row[2])
        source = dict(zip(APPLICANT_INDEX_COLUMNS, row[3:], strict=True))
        desired = us_applicant_name_lookup_row(source)
        if str(row[0]) == str(desired[0]) and str(row[1]) == str(desired[1]):
            continue
        tombstones.append(
            [
                row[0],
                row[1],
                source["serial_number"],
                source["owner_key"],
                source["source_row_hash"],
                source["record_hash"],
                max(old_source_rank, int(source["source_rank"])) + 1,
                1,
            ]
        )
    for offset in range(0, len(tombstones), MAX_BATCH_SIZE):
        _assert_epoch(expected_epoch, serving_epoch_getter)
        client.insert(
            US_APPLICANT_NAME_LOOKUP_TABLE,
            tombstones[offset : offset + MAX_BATCH_SIZE],
            column_names=list(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS),
        )
    _assert_epoch(expected_epoch, serving_epoch_getter)
    return len(rows)
