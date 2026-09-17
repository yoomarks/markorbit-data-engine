from __future__ import annotations

import re
from typing import Any

from app.read_performance_baseline import DEFAULT_QUERY_BUDGET


ATTORNEY_NAME_LOOKUP_SCHEMA_VERSION = "US_ATTORNEY_NAME_LOOKUP_SCHEMA_V1"
ATTORNEY_NAME_LOOKUP_READY_VERSION = "US_ATTORNEY_NAME_LOOKUP_READY_V1"
US_ATTORNEY_NAME_LOOKUP_TABLE = "markorbit_facts.us_attorney_name_candidate_lookup"
MAX_ATTORNEY_MATCHES = 500


class AttorneyNameLookupInvalid(ValueError):
    pass


class AttorneyNameLookupScopeExceeded(RuntimeError):
    pass


class AttorneyNameLookupUnavailable(RuntimeError):
    pass


def normalize_attorney_name(value: str) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    if not name or len(name) > 512:
        raise AttorneyNameLookupInvalid("attorney name must contain 1 to 512 characters")
    return name


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rows(client: Any, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        result = client.query(sql, settings=settings)
    except Exception as exc:
        raise AttorneyNameLookupUnavailable(str(exc)) from exc
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def attorneys_by_name(client: Any, name: str) -> dict[str, Any]:
    normalized_name = normalize_attorney_name(name)
    budget = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_ATTORNEY_MATCHES + 1}
    readiness = _rows(
        client,
        """
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'US_ATTORNEY_NAME_LOOKUP'
        LIMIT 1
        """,
        budget,
    )
    if not readiness or str(readiness[0]["version"]) != ATTORNEY_NAME_LOOKUP_READY_VERSION:
        raise AttorneyNameLookupUnavailable(
            "US attorney name lookup is not backfilled and accepted"
        )

    candidates = _rows(
        client,
        f"""
        SELECT serial_number, correspondent_key
        FROM {US_ATTORNEY_NAME_LOOKUP_TABLE} FINAL
        WHERE normalized_name = {_sql_literal(normalized_name)}
          AND is_deleted = 0
        ORDER BY normalized_name, serial_number, correspondent_key
        LIMIT {MAX_ATTORNEY_MATCHES + 1}
        """,
        budget,
    )
    if len(candidates) > MAX_ATTORNEY_MATCHES:
        raise AttorneyNameLookupScopeExceeded(
            f"attorney name resolves to more than {MAX_ATTORNEY_MATCHES} candidate records"
        )
    candidate_serials = list(dict.fromkeys(str(row["serial_number"]) for row in candidates))
    matches: list[dict[str, Any]] = []
    if candidate_serials:
        serial_sql = ", ".join(_sql_literal(value) for value in candidate_serials)
        matches = _rows(
            client,
            f"""
            SELECT serial_number, correspondent_key, attorney_name, attorney_docket_number,
                   source_package_kind, source_effective_date, source_file, source_row_hash,
                   toString(last_source_package_id) AS source_package_id, record_hash,
                   source_rank, ingested_at
            FROM markorbit_facts.us_correspondent_current FINAL
            WHERE serial_number IN ({serial_sql})
              AND lowerUTF8(replaceRegexpAll(trimBoth(attorney_name), '\\\\s+', ' ')) =
                  {_sql_literal(normalized_name)}
              AND is_deleted = 0
            ORDER BY serial_number, correspondent_key
            LIMIT {MAX_ATTORNEY_MATCHES}
            """,
            budget,
        )
    return {
        "input_name": str(name).strip(),
        "normalized_name": normalized_name,
        "candidate_count": len(candidate_serials),
        "match_count": len(matches),
        "matches": matches,
        "semantics": "CURRENT_OFFICIAL_USPTO_ATTORNEY_NAME_FACTS_NO_IDENTITY_RESOLUTION",
    }
