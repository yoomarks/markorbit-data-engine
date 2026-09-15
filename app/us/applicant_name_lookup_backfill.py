from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.applicant_name_lookup import (
    US_APPLICANT_NAME_LOOKUP_TABLE,
    US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS,
    us_applicant_name_lookup_row,
)
from app.us.applicant_candidate_index import canonical_identity_text
from app.us.publisher import APPLICANT_INDEX_COLUMNS

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
MAX_POINT_FETCH_SQL_BYTES = 96_000
MAX_POINT_FETCH_ITEMS = 500
STALE_RECONCILE_SHARDS = 4


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




def _bounded_sql_batches(items: list[Any], render: Callable[[Any], str]):
    batch: list[Any] = []
    used = 0
    for item in items:
        fragment = render(item)
        size = len(fragment.encode("utf-8")) + (2 if batch else 0)
        if size > MAX_POINT_FETCH_SQL_BYTES:
            raise RuntimeError("US Applicant NAME point-fetch key exceeds SQL safety budget")
        if batch and (len(batch) >= MAX_POINT_FETCH_ITEMS or used + size > MAX_POINT_FETCH_SQL_BYTES):
            yield batch
            batch = []
            used = 0
            size = len(fragment.encode("utf-8"))
        batch.append(item)
        used += size
    if batch:
        yield batch

def _lookup_sql_text(value: object) -> str:
    return _sql_text(str(value))


def _candidate_key_tuples_sql(keys: list[tuple[str, str, str]]) -> str:
    return ", ".join(
        f"({_lookup_sql_text(candidate_key)}, {_lookup_sql_text(serial)}, {_lookup_sql_text(owner_key)})"
        for candidate_key, serial, owner_key in keys
    )


def _lookup_key_tuples_sql(keys: list[tuple[str, str, str, str]]) -> str:
    return ", ".join(
        f"({_lookup_sql_text(normalized_name)}, {_lookup_sql_text(candidate_key)}, {_lookup_sql_text(serial)}, {_lookup_sql_text(owner_key)})"
        for normalized_name, candidate_key, serial, owner_key in keys
    )


def _fetch_candidate_rows(
    client: Any, keys: list[tuple[str, str, str]]
) -> dict[tuple[str, str, str], list[Any]]:
    result: dict[tuple[str, str, str], list[Any]] = {}
    columns = (
        "candidate_key",
        "serial_number",
        "owner_key",
        "source_row_hash",
        "record_hash",
        "source_rank",
        "party_name_norm",
    )
    projection = ", ".join(columns)
    for batch in _bounded_sql_batches(
        keys,
        lambda item: (
            f"({_lookup_sql_text(item[0])}, {_lookup_sql_text(item[1])}, "
            f"{_lookup_sql_text(item[2])})"
        ),
    ):
        rows = client.query(
            f"""
            SELECT {projection}
            FROM markorbit_facts.us_applicant_candidate_current FINAL
            WHERE is_deleted = 0
              AND (candidate_key, serial_number, owner_key) IN ({_candidate_key_tuples_sql(batch)})
            """,
            settings=READ_SETTINGS,
        ).result_rows
        for row in rows:
            result[(str(row[0]), str(row[1]), str(row[2]))] = list(row)
    return result


def _fetch_lookup_rows(
    client: Any, keys: list[tuple[str, str, str, str]]
) -> dict[tuple[str, str, str, str], list[Any]]:
    result: dict[tuple[str, str, str, str], list[Any]] = {}
    projection = ", ".join(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS)
    for batch in _bounded_sql_batches(
        keys,
        lambda item: (
            f"({_lookup_sql_text(item[0])}, {_lookup_sql_text(item[1])}, "
            f"{_lookup_sql_text(item[2])}, {_lookup_sql_text(item[3])})"
        ),
    ):
        rows = client.query(
            f"""
            SELECT {projection}
            FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL
            WHERE is_deleted = 0
              AND (normalized_name, candidate_key, serial_number, owner_key)
                  IN ({_lookup_key_tuples_sql(batch)})
            """,
            settings=READ_SETTINGS,
        ).result_rows
        for row in rows:
            result[(str(row[0]), str(row[1]), str(row[2]), str(row[3]))] = list(row)
    return result


def reconcile_us_applicant_name_lookup(
    *,
    client: Any,
    max_rows: int = MAX_RECONCILE_ROWS,
    expected_epoch: str | None = None,
    serving_epoch_getter: Callable[[], str] | None = None,
) -> int:
    """Boundedly reconcile NAME lookup bindings using compact large-table joins."""
    if max_rows < 1 or max_rows > MAX_RECONCILE_ROWS:
        raise ValueError(f"max_rows must be between 1 and {MAX_RECONCILE_ROWS}")
    _assert_epoch(expected_epoch, serving_epoch_getter)

    missing = list(
        client.query(
            f"""
            SELECT source.candidate_key, source.serial_number, source.owner_key,
                   source.record_hash, source.source_rank
            FROM
            (
                SELECT candidate_key, serial_number, owner_key, record_hash, source_rank
                FROM markorbit_facts.us_applicant_candidate_current FINAL
                WHERE is_deleted = 0
            ) AS source
            LEFT JOIN
            (
                SELECT candidate_key, serial_number, owner_key, record_hash, source_rank,
                       toUInt8(1) AS present
                FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
              ON target.candidate_key = source.candidate_key
             AND target.serial_number = source.serial_number
             AND target.owner_key = source.owner_key
             AND target.record_hash = source.record_hash
             AND target.source_rank = source.source_rank
            WHERE target.present = 0
            ORDER BY source.candidate_key, source.serial_number, source.owner_key
            LIMIT {max_rows + 1}
            """,
            settings=RECONCILE_READ_SETTINGS,
        ).result_rows
    )
    if len(missing) > max_rows:
        raise RuntimeError(f"US Applicant NAME lookup reconciliation exceeds bounded limit {max_rows}")
    missing_keys = list(dict.fromkeys((str(r[0]), str(r[1]), str(r[2])) for r in missing))
    candidates = _fetch_candidate_rows(client, missing_keys)
    record_index = 4
    rank_index_candidate = 5
    lookup_rows: list[list[Any]] = []
    for candidate_key, serial, owner_key, record_hash, source_rank in missing:
        source_row = candidates.get((str(candidate_key), str(serial), str(owner_key)))
        if source_row is None or str(source_row[record_index]) != str(record_hash) or int(source_row[rank_index_candidate]) != int(source_rank):
            raise RuntimeError("US Applicant NAME lookup source binding drifted during reconciliation")
        normalized_name = canonical_identity_text(source_row[6])
        if not normalized_name:
            raise RuntimeError("US Applicant NAME lookup source normalized name is empty")
        lookup_rows.append([
            normalized_name,
            source_row[0],
            source_row[1],
            source_row[2],
            source_row[3],
            source_row[4],
            source_row[5],
            0,
        ])
    stale: list[list[Any]] = []
    for bucket in range(STALE_RECONCILE_SHARDS):
        remaining = max_rows - len(stale)
        bucket_rows = list(
            client.query(
                f"""
                SELECT target.normalized_name, target.candidate_key, target.source_rank,
                       source.candidate_key, source.serial_number, source.owner_key,
                       source.record_hash, source.source_rank
                FROM
                (
                    SELECT candidate_key, serial_number, owner_key, record_hash, source_rank
                    FROM markorbit_facts.us_applicant_candidate_current FINAL
                    WHERE is_deleted = 0
                      AND cityHash64(serial_number, owner_key) % {STALE_RECONCILE_SHARDS} = {bucket}
                ) AS source
                INNER JOIN
                (
                    SELECT normalized_name, candidate_key, serial_number, owner_key,
                           record_hash, source_rank
                    FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL
                    WHERE is_deleted = 0
                      AND cityHash64(serial_number, owner_key) % {STALE_RECONCILE_SHARDS} = {bucket}
                ) AS target
                  ON target.serial_number = source.serial_number
                 AND target.owner_key = source.owner_key
                WHERE target.record_hash != source.record_hash
                   OR target.source_rank != source.source_rank
                   OR target.candidate_key != source.candidate_key
                ORDER BY source.candidate_key, source.serial_number, source.owner_key,
                         target.normalized_name, target.candidate_key
                LIMIT {remaining + 1}
                """,
                settings=RECONCILE_READ_SETTINGS,
            ).result_rows
        )
        if len(bucket_rows) > remaining:
            raise RuntimeError(
                f"US Applicant NAME lookup stale-binding reconciliation exceeds bounded limit {max_rows}"
            )
        stale.extend(bucket_rows)
    stale_keys = list(dict.fromkeys((str(r[3]), str(r[4]), str(r[5])) for r in stale))
    stale_sources = _fetch_candidate_rows(client, stale_keys)
    source_row_hash_index = 3
    party_name_index = 6
    tombstones: list[list[Any]] = []
    for row in stale:
        old_normalized, old_candidate, old_rank, desired_candidate, serial, owner_key, record_hash, source_rank = row
        source_row = stale_sources.get((str(desired_candidate), str(serial), str(owner_key)))
        if (
            source_row is None
            or str(source_row[record_index]) != str(record_hash)
            or int(source_row[rank_index_candidate]) != int(source_rank)
        ):
            raise RuntimeError("US Applicant NAME lookup stale source binding drifted during reconciliation")
        desired_normalized = canonical_identity_text(source_row[party_name_index])
        if str(old_normalized) == desired_normalized and str(old_candidate) == str(desired_candidate):
            continue
        tombstones.append([
            old_normalized,
            old_candidate,
            serial,
            owner_key,
            source_row[source_row_hash_index],
            record_hash,
            max(int(old_rank), int(source_rank)) + 1,
            1,
        ])
    orphans = list(
        client.query(
            f"""
            SELECT target.normalized_name, target.candidate_key, target.serial_number,
                   target.owner_key, target.source_rank
            FROM
            (
                SELECT normalized_name, candidate_key, serial_number, owner_key, source_rank
                FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL
                WHERE is_deleted = 0
            ) AS target
            LEFT JOIN
            (
                SELECT serial_number, owner_key, toUInt8(1) AS present
                FROM markorbit_facts.us_applicant_candidate_current FINAL
                WHERE is_deleted = 0
            ) AS source
              ON source.serial_number = target.serial_number
             AND source.owner_key = target.owner_key
            WHERE source.present = 0
            ORDER BY target.serial_number, target.owner_key,
                     target.normalized_name, target.candidate_key
            LIMIT {max_rows + 1}
            """,
            settings=RECONCILE_READ_SETTINGS,
        ).result_rows
    )
    if len(orphans) > max_rows:
        raise RuntimeError(f"US Applicant NAME lookup orphan reconciliation exceeds bounded limit {max_rows}")
    orphan_keys = [(str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in orphans]
    lookup_by_key = _fetch_lookup_rows(client, orphan_keys)
    rank_index = list(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS).index("source_rank")
    deleted_index = list(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS).index("is_deleted")
    orphan_tombstones: list[list[Any]] = []
    for normalized_name, candidate_key, serial, owner_key, source_rank in orphans:
        key = (str(normalized_name), str(candidate_key), str(serial), str(owner_key))
        target_row = lookup_by_key.get(key)
        if target_row is None or int(target_row[rank_index]) != int(source_rank):
            raise RuntimeError("US Applicant NAME lookup orphan binding drifted during reconciliation")
        target_row = list(target_row)
        target_row[rank_index] = int(target_row[rank_index]) + 1
        target_row[deleted_index] = 1
        orphan_tombstones.append(target_row)
    mutation_rows = len(lookup_rows) + len(tombstones) + len(orphan_tombstones)
    if mutation_rows > max_rows:
        raise RuntimeError(
            f"US Applicant NAME lookup total reconciliation mutations exceed bounded limit {max_rows}"
        )
    for rows_to_insert in (lookup_rows, tombstones, orphan_tombstones):
        for offset in range(0, len(rows_to_insert), MAX_BATCH_SIZE):
            _assert_epoch(expected_epoch, serving_epoch_getter)
            client.insert(
                US_APPLICANT_NAME_LOOKUP_TABLE,
                rows_to_insert[offset : offset + MAX_BATCH_SIZE],
                column_names=list(US_APPLICANT_NAME_LOOKUP_WRITE_COLUMNS),
            )
    _assert_epoch(expected_epoch, serving_epoch_getter)
    return mutation_rows
