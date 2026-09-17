from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.read_performance_baseline import DEFAULT_QUERY_BUDGET


REGISTRATION_LOOKUP_SCHEMA_VERSION = "US_REGISTRATION_CANDIDATE_LOOKUP_SCHEMA_V1"
REGISTRATION_LOOKUP_READY_VERSION = "US_REGISTRATION_CANDIDATE_LOOKUP_READY_V1"
US_REGISTRATION_LOOKUP_TABLE = "markorbit_facts.us_registration_candidate_lookup"
MAX_REGISTRATION_CANDIDATES = 500
US_REGISTRATION_LOOKUP_COLUMNS: tuple[str, ...] = (
    "registration_number",
    "serial_number",
    "source_row_hash",
    "record_hash",
    "source_package_id",
    "source_rank",
    "observed_at",
)
US_REGISTRATION_LOOKUP_WRITE_COLUMNS: tuple[str, ...] = tuple(
    column for column in US_REGISTRATION_LOOKUP_COLUMNS if column != "observed_at"
)


class RegistrationLookupInvalid(ValueError):
    pass


class RegistrationLookupScopeExceeded(RuntimeError):
    pass


class RegistrationLookupUnavailable(RuntimeError):
    pass


def registration_candidate_row(source: Mapping[str, Any]) -> list[Any] | None:
    registration = str(source.get("registration_number") or "").strip()
    if not registration:
        return None
    return [
        registration,
        source.get("serial_number"),
        source.get("source_row_hash"),
        source.get("record_hash"),
        source.get("last_source_package_id"),
        source.get("source_rank"),
    ]


def _registration_number(value: str) -> str:
    registration = value.strip()
    if not registration.isdigit() or not 1 <= len(registration) <= 12:
        raise RegistrationLookupInvalid("USPTO registration number must contain 1 to 12 digits")
    return registration


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rows(client: Any, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        result = client.query(sql, settings=settings)
    except Exception as exc:
        raise RegistrationLookupUnavailable(str(exc)) from exc
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def lookup_registration(client: Any, registration_number: str) -> dict[str, Any]:
    registration = _registration_number(registration_number)
    budget = {
        **DEFAULT_QUERY_BUDGET,
        "max_result_rows": MAX_REGISTRATION_CANDIDATES + 1,
    }
    readiness = _rows(
        client,
        """
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'US_REGISTRATION_CANDIDATE_LOOKUP'
        LIMIT 1
        """,
        budget,
    )
    if not readiness or str(readiness[0]["version"]) != REGISTRATION_LOOKUP_READY_VERSION:
        raise RegistrationLookupUnavailable(
            "US registration candidate lookup is not backfilled and accepted"
        )
    candidates = _rows(
        client,
        f"""
        SELECT serial_number
        FROM {US_REGISTRATION_LOOKUP_TABLE} FINAL
        WHERE registration_number = '{registration}'
        ORDER BY registration_number, serial_number
        LIMIT {MAX_REGISTRATION_CANDIDATES + 1}
        """,
        budget,
    )
    if len(candidates) > MAX_REGISTRATION_CANDIDATES:
        raise RegistrationLookupScopeExceeded(
            f"registration resolves to more than {MAX_REGISTRATION_CANDIDATES} candidate serials"
        )
    candidate_serials = list(dict.fromkeys(str(row["serial_number"]) for row in candidates))
    if not candidate_serials:
        return {
            "registration_number": registration,
            "candidate_count": 0,
            "match_count": 0,
            "trademarks": [],
            "semantics": "CURRENT_USPTO_CASE_FACTS_NOT_LEGAL_STATUS_CONCLUSION",
        }

    serial_sql = ", ".join(_sql_literal(value) for value in candidate_serials)
    trademarks = _rows(
        client,
        f"""
        SELECT serial_number, registration_number, filing_date, registration_date,
               status_code, status_date, mark_identification,
               source_package_kind, source_effective_date, source_file,
               toString(last_source_package_id) AS source_package_id,
               source_row_hash, record_hash, source_rank
        FROM markorbit_facts.us_case_current FINAL
        WHERE serial_number IN ({serial_sql})
          AND registration_number = '{registration}'
          AND is_deleted = 0
        ORDER BY serial_number
        LIMIT {MAX_REGISTRATION_CANDIDATES}
        """,
        budget,
    )
    return {
        "registration_number": registration,
        "candidate_count": len(candidate_serials),
        "match_count": len(trademarks),
        "trademarks": trademarks,
        "semantics": "CURRENT_USPTO_CASE_FACTS_NOT_LEGAL_STATUS_CONCLUSION",
    }
