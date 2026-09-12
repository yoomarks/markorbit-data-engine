from __future__ import annotations

from typing import Any

from app.applicant_name_lookup import (
    APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
    CN_APPLICANT_NAME_LOOKUP_TABLE,
)
from app.cn.text import normalized_match_text

READ_SETTINGS = {"max_threads": 1, "max_rows_to_read": 100_000_000, "read_overflow_mode": "throw"}
EXPECTED_SORTING_KEY = "normalized_name, entity_id, application_number, relation_key"
BINDING_HASH = "cityHash64(concat(toString(entity_id), '\\x1f', application_number, '\\x1f', toString(relation_key), '\\x1f', toString(record_hash), '\\x1f', toString(source_rank)))"


def _one(result: Any) -> tuple[Any, ...]:
    rows = list(result.result_rows)
    if len(rows) != 1:
        raise RuntimeError(f"expected exactly one result row, observed={len(rows)}")
    return tuple(rows[0])


def _stats(table: str, where: str) -> str:
    return f"SELECT count(), toString(sum({BINDING_HASH})), toString(groupBitXor({BINDING_HASH})) FROM {table} FINAL WHERE {where}"


def verify_cn_applicant_name_lookup(client: Any, sample_limit: int = 200) -> dict[str, object]:
    schema = list(
        client.query(
            "SELECT name, engine, sorting_key FROM system.tables WHERE database = 'markorbit_facts' AND name IN ('cn_applicant_name_lookup_current', 'cn_applicant_name_lookup_from_case_party_mv')",
            settings=READ_SETTINGS,
        ).result_rows
    )
    by_name = {str(row[0]): tuple(map(str, row[1:])) for row in schema}
    target = by_name.get("cn_applicant_name_lookup_current")
    projection = by_name.get("cn_applicant_name_lookup_from_case_party_mv")
    if target is None or projection is None:
        return {
            "schema_version": APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
            "complete": False,
            "reason": "LOOKUP_PROJECTION_MISSING",
        }
    source = _one(
        client.query(
            _stats(
                "markorbit_facts.cn_case_party_current",
                "is_deleted = 0 AND is_current = 1 AND role IN ('OWNER', 'CO_OWNER') AND entity_id IS NOT NULL AND normalized_name != ''",
            ),
            settings=READ_SETTINGS,
        )
    )
    lookup = _one(
        client.query(
            _stats(CN_APPLICANT_NAME_LOOKUP_TABLE, "is_deleted = 0"), settings=READ_SETTINGS
        )
    )
    samples = client.query(
        f"""
        SELECT lookup.normalized_name, source.raw_name
        FROM (SELECT * FROM {CN_APPLICANT_NAME_LOOKUP_TABLE} FINAL WHERE is_deleted = 0) AS lookup
        INNER JOIN
        (
            SELECT * FROM markorbit_facts.cn_case_party_current FINAL
            WHERE is_deleted = 0 AND is_current = 1
        ) AS source
          ON source.entity_id = lookup.entity_id
         AND source.application_number = lookup.application_number
         AND source.relation_key = lookup.relation_key
        ORDER BY lookup.normalized_name
        LIMIT {sample_limit}
        """,
        settings=READ_SETTINGS,
    )
    mismatches = sum(
        1
        for normalized, raw in samples.result_rows
        if str(normalized) != normalized_match_text(raw)
    )
    schema_match = (
        target[0] == "ReplacingMergeTree"
        and target[1].replace("`", "").replace("(", "").replace(")", "").strip()
        == EXPECTED_SORTING_KEY
        and projection[0] == "MaterializedView"
    )
    count_match = int(source[0]) == int(lookup[0])
    checksum_match = tuple(map(str, source[1:])) == tuple(map(str, lookup[1:]))
    return {
        "schema_version": APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
        "complete": schema_match and count_match and checksum_match and mismatches == 0,
        "schema_match": schema_match,
        "count_match": count_match,
        "binding_checksum_match": checksum_match,
        "source_visible_rows": int(source[0]),
        "lookup_visible_rows": int(lookup[0]),
        "sample_checked": len(samples.result_rows),
        "sample_mismatches": mismatches,
    }
