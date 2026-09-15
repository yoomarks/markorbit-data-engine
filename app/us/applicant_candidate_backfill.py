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
RECONCILE_READ_SETTINGS = {
    **READ_SETTINGS,
    "max_rows_to_read": 150_000_000,
    "join_algorithm": "full_sorting_merge",
    "max_bytes_before_external_sort": 268_435_456,
    "max_memory_usage": 4_294_967_296,
}
POINT_FETCH_BATCH_SIZE = 2_000


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


def _identity_tuples_sql(identities: list[tuple[str, str]]) -> str:
    return ", ".join(
        f"({_sql_text(serial)}, {_sql_text(owner_key)})"
        for serial, owner_key in identities
    )


def _candidate_tuples_sql(keys: list[tuple[str, str, str]]) -> str:
    return ", ".join(
        f"({_sql_text(candidate_key)}, {_sql_text(serial)}, {_sql_text(owner_key)})"
        for candidate_key, serial, owner_key in keys
    )


def _fetch_owner_rows(client: Any, identities: list[tuple[str, str]]) -> dict[tuple[str, str], list[Any]]:
    result: dict[tuple[str, str], list[Any]] = {}
    columns_sql = ", ".join(OWNER_COLUMNS)
    for offset in range(0, len(identities), POINT_FETCH_BATCH_SIZE):
        batch = identities[offset : offset + POINT_FETCH_BATCH_SIZE]
        if not batch:
            continue
        rows = client.query(
            f"""
            SELECT {columns_sql}
            FROM markorbit_facts.us_owner_current FINAL
            WHERE is_deleted = 0
              AND (serial_number, owner_key) IN ({_identity_tuples_sql(batch)})
            """,
            settings=READ_SETTINGS,
        ).result_rows
        serial_index = OWNER_COLUMNS.index("serial_number")
        owner_index = OWNER_COLUMNS.index("owner_key")
        for row in rows:
            result[(str(row[serial_index]), str(row[owner_index]))] = list(row)
    return result


def _fetch_candidate_rows(
    client: Any, keys: list[tuple[str, str, str]]
) -> dict[tuple[str, str, str], list[Any]]:
    result: dict[tuple[str, str, str], list[Any]] = {}
    columns_sql = ", ".join(APPLICANT_INDEX_COLUMNS)
    for offset in range(0, len(keys), POINT_FETCH_BATCH_SIZE):
        batch = keys[offset : offset + POINT_FETCH_BATCH_SIZE]
        if not batch:
            continue
        rows = client.query(
            f"""
            SELECT {columns_sql}
            FROM {APPLICANT_INDEX_TABLE} FINAL
            WHERE is_deleted = 0
              AND (candidate_key, serial_number, owner_key) IN ({_candidate_tuples_sql(batch)})
            """,
            settings=READ_SETTINGS,
        ).result_rows
        candidate_index = APPLICANT_INDEX_COLUMNS.index("candidate_key")
        serial_index = APPLICANT_INDEX_COLUMNS.index("serial_number")
        owner_index = APPLICANT_INDEX_COLUMNS.index("owner_key")
        for row in rows:
            result[
                (str(row[candidate_index]), str(row[serial_index]), str(row[owner_index]))
            ] = list(row)
    return result


def _unique_identities(rows: list[tuple[Any, ...]], serial_index: int, owner_index: int) -> list[tuple[str, str]]:
    return list(dict.fromkeys((str(row[serial_index]), str(row[owner_index])) for row in rows))


def reconcile_us_applicant_candidate_index(
    *,
    client: Any,
    max_rows: int = MAX_RECONCILE_ROWS,
    expected_epoch: str | None = None,
    serving_epoch_getter: Callable[[], str] | None = None,
) -> int:
    """Boundedly reconcile candidate bindings without carrying wide rows through large joins."""
    if max_rows < 1 or max_rows > MAX_RECONCILE_ROWS:
        raise ValueError(f"max_rows must be between 1 and {MAX_RECONCILE_ROWS}")
    _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)

    missing = list(
        client.query(
            f"""
            SELECT source.serial_number, source.owner_key, source.record_hash, source.source_rank
            FROM
            (
                SELECT serial_number, owner_key, record_hash, source_rank
                FROM markorbit_facts.us_owner_current FINAL
                WHERE is_deleted = 0
            ) AS source
            LEFT JOIN
            (
                SELECT serial_number, owner_key, record_hash, source_rank, toUInt8(1) AS present
                FROM {APPLICANT_INDEX_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
             ON target.serial_number = source.serial_number
             AND target.owner_key = source.owner_key
             AND target.record_hash = source.record_hash
             AND target.source_rank = source.source_rank
            WHERE target.present = 0
            ORDER BY source.serial_number, source.owner_key
            LIMIT {max_rows + 1}
            """,
            settings=RECONCILE_READ_SETTINGS,
        ).result_rows
    )
    if len(missing) > max_rows:
        raise RuntimeError(f"US Applicant candidate reconciliation exceeds bounded limit {max_rows}")
    owner_rows = _fetch_owner_rows(client, _unique_identities(missing, 0, 1))
    index_rows: list[list[Any]] = []
    record_index = OWNER_COLUMNS.index("record_hash")
    rank_index_owner = OWNER_COLUMNS.index("source_rank")
    for serial, owner_key, record_hash, source_rank in missing:
        owner_row = owner_rows.get((str(serial), str(owner_key)))
        if owner_row is None or str(owner_row[record_index]) != str(record_hash) or int(owner_row[rank_index_owner]) != int(source_rank):
            raise RuntimeError("US Applicant candidate source binding drifted during reconciliation")
        index_rows.append(applicant_index_row(owner_row, OWNER_COLUMNS))
    for offset in range(0, len(index_rows), MAX_BATCH_SIZE):
        _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
        client.insert(APPLICANT_INDEX_TABLE, index_rows[offset : offset + MAX_BATCH_SIZE], column_names=APPLICANT_INDEX_COLUMNS)

    stale = list(
        client.query(
            f"""
            SELECT target.candidate_key, target.source_rank,
                   source.serial_number, source.owner_key, source.record_hash, source.source_rank
            FROM
            (
                SELECT serial_number, owner_key, record_hash, source_rank
                FROM markorbit_facts.us_owner_current FINAL
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
            settings=RECONCILE_READ_SETTINGS,
        ).result_rows
    )
    if len(stale) > max_rows:
        raise RuntimeError(f"US Applicant candidate stale-binding reconciliation exceeds bounded limit {max_rows}")
    stale_owner_rows = _fetch_owner_rows(client, _unique_identities(stale, 2, 3))
    tombstones: list[list[Any]] = []
    deleted_index_owner = OWNER_COLUMNS.index("is_deleted")
    for old_candidate_key, old_source_rank, serial, owner_key, record_hash, source_rank in stale:
        owner_row = stale_owner_rows.get((str(serial), str(owner_key)))
        if owner_row is None or str(owner_row[record_index]) != str(record_hash) or int(owner_row[rank_index_owner]) != int(source_rank):
            raise RuntimeError("US Applicant candidate stale source binding drifted during reconciliation")
        if str(old_candidate_key) == str(applicant_index_row(owner_row, OWNER_COLUMNS)[0]):
            continue
        owner_row = list(owner_row)
        owner_row[deleted_index_owner] = 1
        owner_row[rank_index_owner] = max(int(old_source_rank), int(owner_row[rank_index_owner])) + 1
        tombstones.append([str(old_candidate_key), *owner_row])
    for offset in range(0, len(tombstones), MAX_BATCH_SIZE):
        _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
        client.insert(APPLICANT_INDEX_TABLE, tombstones[offset : offset + MAX_BATCH_SIZE], column_names=APPLICANT_INDEX_COLUMNS)

    orphans = list(
        client.query(
            f"""
            SELECT target.candidate_key, target.serial_number, target.owner_key, target.source_rank
            FROM
            (
                SELECT candidate_key, serial_number, owner_key, source_rank
                FROM {APPLICANT_INDEX_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
            LEFT JOIN
            (
                SELECT serial_number, owner_key, toUInt8(1) AS present
                FROM markorbit_facts.us_owner_current FINAL
                WHERE is_deleted = 0
            ) AS source
              ON source.serial_number = target.serial_number
             AND source.owner_key = target.owner_key
            WHERE source.present = 0
            ORDER BY target.serial_number, target.owner_key, target.candidate_key
            LIMIT {max_rows + 1}
            """,
            settings=RECONCILE_READ_SETTINGS,
        ).result_rows
    )
    if len(orphans) > max_rows:
        raise RuntimeError(f"US Applicant candidate orphan reconciliation exceeds bounded limit {max_rows}")
    orphan_keys = [(str(row[0]), str(row[1]), str(row[2])) for row in orphans]
    target_rows = _fetch_candidate_rows(client, orphan_keys)
    rank_index = APPLICANT_INDEX_COLUMNS.index("source_rank")
    deleted_index = APPLICANT_INDEX_COLUMNS.index("is_deleted")
    orphan_tombstones: list[list[Any]] = []
    for candidate_key, serial, owner_key, source_rank in orphans:
        key = (str(candidate_key), str(serial), str(owner_key))
        target_row = target_rows.get(key)
        if target_row is None or int(target_row[rank_index]) != int(source_rank):
            raise RuntimeError("US Applicant candidate orphan binding drifted during reconciliation")
        target_row = list(target_row)
        target_row[rank_index] = int(target_row[rank_index]) + 1
        target_row[deleted_index] = 1
        orphan_tombstones.append(target_row)
    for offset in range(0, len(orphan_tombstones), MAX_BATCH_SIZE):
        _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
        client.insert(APPLICANT_INDEX_TABLE, orphan_tombstones[offset : offset + MAX_BATCH_SIZE], column_names=APPLICANT_INDEX_COLUMNS)
    _assert_epoch(expected_epoch=expected_epoch, serving_epoch_getter=serving_epoch_getter)
    return len(index_rows) + len(tombstones) + len(orphan_tombstones)
