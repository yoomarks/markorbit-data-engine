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
    source_projection = ", ".join(f"source.{column}" for column in APPLICANT_INDEX_COLUMNS)
    sample = client.query(
        f"""
        SELECT lookup.normalized_name, {source_projection}
        FROM
        (
            SELECT * FROM {US_APPLICANT_NAME_LOOKUP_TABLE} FINAL WHERE is_deleted = 0
        ) AS lookup
        INNER JOIN
        (
            SELECT * FROM markorbit_facts.us_applicant_candidate_current FINAL WHERE is_deleted = 0
        ) AS source
          ON source.candidate_key = lookup.candidate_key
         AND source.serial_number = lookup.serial_number
         AND source.owner_key = lookup.owner_key
        ORDER BY lookup.normalized_name, lookup.candidate_key, lookup.serial_number, lookup.owner_key
        LIMIT {sample_limit}
        """,
        settings=READ_SETTINGS,
    )
    mismatches = 0
    checked = 0
    for row in sample.result_rows:
        source = dict(zip(APPLICANT_INDEX_COLUMNS, row[1:], strict=True))
        expected = us_applicant_name_lookup_row(source)
        checked += 1
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
