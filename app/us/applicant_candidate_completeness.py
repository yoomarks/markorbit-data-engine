from __future__ import annotations

from typing import Any

from app.us.applicant_candidate_index import (
    APPLICANT_INDEX_SCHEMA_VERSION,
    APPLICANT_INDEX_TABLE,
    IDENTITY_FIELDS,
    applicant_candidate_key,
)

READ_SETTINGS = {
    "max_threads": 1,
    "max_rows_to_read": 100_000_000,
    "read_overflow_mode": "throw",
}
_EXPECTED_SORTING_KEY = "candidate_key, serial_number, owner_key"
_BINDING_HASH = (
    "cityHash64(concat(serial_number, '\\x1f', toString(owner_key), '\\x1f', "
    "toString(record_hash), '\\x1f', toString(source_rank)))"
)


def _stats_sql(table: str) -> str:
    return f"""
        SELECT
            count() AS visible_rows,
            toString(sum({_BINDING_HASH})) AS hash_sum,
            toString(groupBitXor({_BINDING_HASH})) AS hash_xor
        FROM {table} FINAL
        WHERE is_deleted = 0
    """


def _one_row(result: Any) -> tuple[Any, ...]:
    rows = list(result.result_rows)
    if len(rows) != 1:
        raise RuntimeError(f"expected exactly one result row, observed={len(rows)}")
    return tuple(rows[0])


def _sample_candidate_keys(client: Any, limit: int = 200) -> tuple[int, int]:
    projection = ", ".join(("candidate_key", *IDENTITY_FIELDS))
    result = client.query(
        f"""
        SELECT {projection}
        FROM {APPLICANT_INDEX_TABLE} FINAL
        WHERE is_deleted = 0
        ORDER BY candidate_key, serial_number, owner_key
        LIMIT {limit}
        """,
        settings=READ_SETTINGS,
    )
    checked = 0
    mismatches = 0
    for row in result.result_rows:
        candidate_key = str(row[0])
        owner = dict(zip(IDENTITY_FIELDS, row[1:], strict=True))
        checked += 1
        if applicant_candidate_key(owner) != candidate_key:
            mismatches += 1
    return checked, mismatches


def verify_us_applicant_candidate_index(client: Any) -> dict[str, object]:
    """Return a secret-free completeness receipt; perform no mutation."""
    schema = client.query(
        """
        SELECT engine, sorting_key
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name = 'us_applicant_candidate_current'
        LIMIT 1
        """,
        settings=READ_SETTINGS,
    )
    schema_rows = list(schema.result_rows)
    if not schema_rows:
        return {
            "schema_version": APPLICANT_INDEX_SCHEMA_VERSION,
            "complete": False,
            "reason": "INDEX_TABLE_MISSING",
        }

    engine, sorting_key = (str(value) for value in schema_rows[0])
    source_stats = _one_row(
        client.query(
            _stats_sql("markorbit_facts.us_owner_current"),
            settings=READ_SETTINGS,
        )
    )
    index_stats = _one_row(
        client.query(_stats_sql(APPLICANT_INDEX_TABLE), settings=READ_SETTINGS)
    )
    source_rows, source_sum, source_xor = source_stats
    index_rows, index_sum, index_xor = index_stats
    sample_checked, sample_mismatches = _sample_candidate_keys(client)

    count_match = int(source_rows) == int(index_rows)
    checksum_match = (str(source_sum), str(source_xor)) == (
        str(index_sum),
        str(index_xor),
    )
    schema_match = (
        engine == "ReplacingMergeTree"
        and sorting_key.replace("`", "").replace("(", "").replace(")", "").strip()
        == _EXPECTED_SORTING_KEY
    )
    complete = count_match and checksum_match and schema_match and sample_mismatches == 0
    return {
        "schema_version": APPLICANT_INDEX_SCHEMA_VERSION,
        "complete": complete,
        "source_visible_rows": int(source_rows),
        "index_visible_rows": int(index_rows),
        "count_match": count_match,
        "binding_checksum_match": checksum_match,
        "source_binding_hash_sum": str(source_sum),
        "source_binding_hash_xor": str(source_xor),
        "index_binding_hash_sum": str(index_sum),
        "index_binding_hash_xor": str(index_xor),
        "engine": engine,
        "sorting_key": sorting_key,
        "schema_match": schema_match,
        "candidate_key_sample_checked": sample_checked,
        "candidate_key_sample_mismatches": sample_mismatches,
    }
