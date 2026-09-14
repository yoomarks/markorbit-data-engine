from __future__ import annotations

from typing import Any

from app.applicant_name_lookup import (
    APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
    US_APPLICANT_NAME_LOOKUP_TABLE,
    us_applicant_name_lookup_row,
)
from app.us.publisher import APPLICANT_INDEX_COLUMNS

READ_SETTINGS = {"max_threads": 1, "max_rows_to_read": 100_000_000, "read_overflow_mode": "throw"}
EXPECTED_SORTING_KEY = "normalized_name, candidate_key, serial_number, owner_key"
BINDING_HASH = (
    "cityHash64(concat(candidate_key, '\\x1f', serial_number, '\\x1f', "
    "toString(owner_key), '\\x1f', toString(record_hash), '\\x1f', toString(source_rank)))"
)


def _stats_sql(table: str) -> str:
    return f"SELECT count(), toString(sum({BINDING_HASH})), toString(groupBitXor({BINDING_HASH})) FROM {table} FINAL WHERE is_deleted = 0"


def _one_row(result: Any) -> tuple[Any, ...]:
    rows = list(result.result_rows)
    if len(rows) != 1:
        raise RuntimeError(f"expected exactly one result row, observed={len(rows)}")
    return tuple(rows[0])


def _sql_string(value: object) -> str:
    text = str(value)
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def verify_us_applicant_name_lookup(client: Any, sample_limit: int = 200) -> dict[str, object]:
    schema_rows = list(
        client.query(
            "SELECT engine, sorting_key FROM system.tables WHERE database = 'markorbit_facts' AND name = 'us_applicant_name_lookup_current' LIMIT 1",
            settings=READ_SETTINGS,
        ).result_rows
    )
    if not schema_rows:
        return {
            "schema_version": APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
            "complete": False,
            "reason": "LOOKUP_TABLE_MISSING",
        }

    engine, sorting_key = (str(value) for value in schema_rows[0])
    source_stats = _one_row(
        client.query(
            _stats_sql("markorbit_facts.us_applicant_candidate_current"), settings=READ_SETTINGS
        )
    )
    lookup_stats = _one_row(
        client.query(_stats_sql(US_APPLICANT_NAME_LOOKUP_TABLE), settings=READ_SETTINGS)
    )
    lookup_sample = list(
        client.query(
            f"""
        SELECT normalized_name, candidate_key, serial_number, owner_key
        FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL
        WHERE is_deleted = 0
        ORDER BY normalized_name, candidate_key, serial_number, owner_key
        LIMIT {sample_limit}
        """,
            settings=READ_SETTINGS,
        ).result_rows
    )
    source_by_binding: dict[tuple[str, str, str], dict[str, Any]] = {}
    if lookup_sample:
        bindings = ", ".join(
            "(" + ", ".join(_sql_string(value) for value in row[1:4]) + ")" for row in lookup_sample
        )
        source_rows = client.query(
            f"""
            SELECT {", ".join(APPLICANT_INDEX_COLUMNS)}
            FROM markorbit_facts.us_applicant_candidate_current FINAL
            WHERE is_deleted = 0
              AND (candidate_key, serial_number, owner_key) IN ({bindings})
            """,
            settings=READ_SETTINGS,
        )
        for row in source_rows.result_rows:
            source = dict(zip(APPLICANT_INDEX_COLUMNS, row, strict=True))
            source_by_binding[
                tuple(
                    str(source[column])
                    for column in ("candidate_key", "serial_number", "owner_key")
                )
            ] = source
    mismatches = 0
    checked = len(lookup_sample)
    for row in lookup_sample:
        source = source_by_binding.get(tuple(str(value) for value in row[1:4]))
        if source is None:
            mismatches += 1
            continue
        expected = us_applicant_name_lookup_row(source)
        if str(row[0]) != str(expected[0]) or str(source["candidate_key"]) != str(expected[1]):
            mismatches += 1

    count_match = int(source_stats[0]) == int(lookup_stats[0])
    checksum_match = tuple(map(str, source_stats[1:])) == tuple(map(str, lookup_stats[1:]))
    schema_match = (
        engine == "ReplacingMergeTree"
        and sorting_key.replace("`", "").replace("(", "").replace(")", "").strip()
        == EXPECTED_SORTING_KEY
    )
    return {
        "schema_version": APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
        "complete": count_match and checksum_match and schema_match and mismatches == 0,
        "source_visible_rows": int(source_stats[0]),
        "lookup_visible_rows": int(lookup_stats[0]),
        "count_match": count_match,
        "binding_checksum_match": checksum_match,
        "engine": engine,
        "sorting_key": sorting_key,
        "schema_match": schema_match,
        "sample_checked": checked,
        "sample_mismatches": mismatches,
    }
