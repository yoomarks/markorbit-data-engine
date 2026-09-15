from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.us.applicant_candidate_index import APPLICANT_INDEX_TABLE, applicant_index_row
from app.us.publisher import APPLICANT_INDEX_COLUMNS, OWNER_COLUMNS

DEFAULT_BATCH_SIZE = 5_000
MAX_BATCH_SIZE = 20_000
MAX_RECONCILE_ROWS = 500_000
READ_SETTINGS = {
    "max_threads": 1,
    "max_rows_to_read": 100_000_000,
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
    stop_requested: Callable[[], bool] | None = None,
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
        if stop_requested is not None and stop_requested():
            raise InterruptedError("US Applicant backfill stop requested")
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


def reconcile_us_applicant_candidate_index(
    *,
    client: Any,
    max_rows: int = MAX_RECONCILE_ROWS,
    expected_epoch: str | None = None,
    serving_epoch_getter: Callable[[], str] | None = None,
) -> int:
    """Boundedly reconcile missing, superseded, and orphan candidate bindings."""
    if max_rows < 1 or max_rows > MAX_RECONCILE_ROWS:
        raise ValueError(f"max_rows must be between 1 and {MAX_RECONCILE_ROWS}")
    _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
    columns_sql = ", ".join(f"source.{column}" for column in OWNER_COLUMNS)
    rows = list(
        client.query(
            f"""
            SELECT {columns_sql}
            FROM
            (
                SELECT * FROM markorbit_facts.us_owner_current FINAL
                WHERE is_deleted = 0
            ) AS source
            LEFT ANTI JOIN
            (
                SELECT serial_number, owner_key, record_hash, source_rank
                FROM {APPLICANT_INDEX_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
             ON target.serial_number = source.serial_number
             AND target.owner_key = source.owner_key
             AND target.record_hash = source.record_hash
             AND target.source_rank = source.source_rank
            ORDER BY source.serial_number, source.owner_key
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
            f"US Applicant candidate reconciliation exceeds bounded limit {max_rows}"
        )
    if rows:
        index_rows = [applicant_index_row(row, OWNER_COLUMNS) for row in rows]
        for offset in range(0, len(index_rows), MAX_BATCH_SIZE):
            _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
            client.insert(
                APPLICANT_INDEX_TABLE,
                index_rows[offset : offset + MAX_BATCH_SIZE],
                column_names=APPLICANT_INDEX_COLUMNS,
            )

    stale_rows = list(
        client.query(
            f"""
            SELECT target.candidate_key, target.source_rank, {columns_sql}
            FROM
            (
                SELECT * FROM markorbit_facts.us_owner_current FINAL
                WHERE is_deleted = 0
            ) AS source
            INNER JOIN
            (
                SELECT candidate_key, serial_number, owner_key, record_hash, source_rank
                FROM {APPLICANT_INDEX_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
              ON target.serial_number = source.serial_number
             AND target.owner_key = source.owner_key
            WHERE target.record_hash != source.record_hash
               OR target.source_rank != source.source_rank
            ORDER BY source.serial_number, source.owner_key, target.candidate_key
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
            f"US Applicant candidate stale-binding reconciliation exceeds bounded limit {max_rows}"
        )
    tombstones = []
    for row in stale_rows:
        old_candidate_key = str(row[0])
        old_source_rank = int(row[1])
        owner_row = list(row[2:])
        if old_candidate_key == applicant_index_row(owner_row, OWNER_COLUMNS)[0]:
            continue
        owner_row[OWNER_COLUMNS.index("is_deleted")] = 1
        owner_row[OWNER_COLUMNS.index("source_rank")] = (
            max(old_source_rank, int(owner_row[OWNER_COLUMNS.index("source_rank")])) + 1
        )
        tombstones.append([old_candidate_key, *owner_row])
    for offset in range(0, len(tombstones), MAX_BATCH_SIZE):
        _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
        client.insert(
            APPLICANT_INDEX_TABLE,
            tombstones[offset : offset + MAX_BATCH_SIZE],
            column_names=APPLICANT_INDEX_COLUMNS,
        )

    target_projection = ", ".join(f"target.{column}" for column in APPLICANT_INDEX_COLUMNS)
    orphan_rows = list(
        client.query(
            f"""
            SELECT {target_projection}
            FROM
            (
                SELECT * FROM {APPLICANT_INDEX_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
            LEFT ANTI JOIN
            (
                SELECT serial_number, owner_key
                FROM markorbit_facts.us_owner_current FINAL
                WHERE is_deleted = 0
            ) AS source
              ON source.serial_number = target.serial_number
             AND source.owner_key = target.owner_key
            ORDER BY target.serial_number, target.owner_key, target.candidate_key
            LIMIT {max_rows + 1}
            """,
            settings={
                **READ_SETTINGS,
                "max_rows_to_read": 100_000_000,
                "join_algorithm": "grace_hash",
            },
        ).result_rows
    )
    if len(orphan_rows) > max_rows:
        raise RuntimeError(
            f"US Applicant candidate orphan reconciliation exceeds bounded limit {max_rows}"
        )
    rank_index = APPLICANT_INDEX_COLUMNS.index("source_rank")
    deleted_index = APPLICANT_INDEX_COLUMNS.index("is_deleted")
    orphan_tombstones = []
    for row in orphan_rows:
        tombstone = list(row)
        tombstone[rank_index] = int(tombstone[rank_index]) + 1
        tombstone[deleted_index] = 1
        orphan_tombstones.append(tombstone)
    for offset in range(0, len(orphan_tombstones), MAX_BATCH_SIZE):
        _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
        client.insert(
            APPLICANT_INDEX_TABLE,
            orphan_tombstones[offset : offset + MAX_BATCH_SIZE],
            column_names=APPLICANT_INDEX_COLUMNS,
        )
    _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
    return len(rows) + len(tombstones) + len(orphan_tombstones)
