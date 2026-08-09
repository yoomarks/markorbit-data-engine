from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.db import clickhouse_client
from app.us.change_history import build_case_timeline
from app.us.deadline_evidence import resolve_case_deadline_evidence
from app.us.maintenance import calculate_maintenance_schedule
from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION
from app.us_ttab import TTAB_SCHEMA_VERSION, TTAB_SEMANTICS
from app.us_ttab.read_model import proceedings_for_serial


CASE_360_VERSION = "US_CASE_360_M1.0"
CASE_360_SEMANTICS = (
    "COMPOSITE_READ_VIEW_PRESERVES_SOURCE_BOUNDARIES_NOT_LEGAL_STATUS_OR_RIGHTS_CONCLUSION"
)
APPLICATION_SEMANTICS = "OFFICIAL_USPTO_CASE_FACTS_NOT_LEGAL_INTERPRETATION"
HISTORY_SEMANTICS = "DURABLE_SOURCE_OBSERVATIONS_WITH_DERIVED_CHANGE_DIFFS"
ASSIGNMENT_SEMANTICS = "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION"
DEADLINE_SEMANTICS = "REVIEWED_EVENT_EVIDENCE_NOT_APPLICATION_LEGAL_STATUS"
MAINTENANCE_SEMANTICS = "DEADLINE_CALCULATION_NOT_CASE_LEGAL_STATUS"


def validate_serial_number(value: str) -> str:
    serial = value.strip()
    if len(serial) != 8 or not serial.isdigit():
        raise ValueError("serial_number must contain exactly 8 digits")
    return serial


def _normalize_value(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    if isinstance(value, tuple):
        return tuple(_normalize_value(item) for item in value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def _query(sql: str) -> list[dict[str, Any]]:
    result = clickhouse_client().query(sql)
    return [
        {
            name: _normalize_value(value)
            for name, value in zip(result.column_names, row, strict=True)
        }
        for row in result.result_rows
    ]


def _application_snapshot(serial: str) -> dict[str, Any] | None:
    case_rows = _query(
        f"""
        SELECT *
        FROM markorbit_facts.us_case_current FINAL
        WHERE serial_number = '{serial}' AND is_deleted = 0
        LIMIT 1
        """
    )
    if not case_rows:
        return None

    correspondent = _query(
        f"""
        SELECT *
        FROM markorbit_facts.us_correspondent_current FINAL
        WHERE serial_number = '{serial}' AND is_deleted = 0
        ORDER BY correspondent_key
        LIMIT 1
        """
    )
    return {
        "case": case_rows[0],
        "owners": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_owner_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            ORDER BY entry_number, owner_key
            """
        ),
        "classifications": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_classification_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            ORDER BY primary_code, classification_key
            """
        ),
        "events": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_event_history FINAL
            WHERE serial_number = '{serial}'
            ORDER BY event_date, event_sequence, event_code
            """
        ),
        "statements": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_statement_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            ORDER BY type_code, statement_key
            """
        ),
        "correspondent": correspondent[0] if correspondent else None,
        "design_searches": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_design_search_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            ORDER BY code, design_search_key
            """
        ),
        "prior_registrations": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_prior_registration_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            ORDER BY relationship_type, number, prior_registration_key
            """
        ),
        "foreign_applications": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_foreign_application_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            ORDER BY entry_number, filing_date, foreign_application_key
            """
        ),
        "madrid_filings": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_madrid_filing_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            ORDER BY entry_number, original_filing_date_uspto, madrid_filing_key
            """
        ),
        "madrid_events": _query(
            f"""
            SELECT *
            FROM markorbit_facts.us_madrid_event_history FINAL
            WHERE serial_number = '{serial}'
            ORDER BY event_date, filing_entry_number, event_entry_number, code
            """
        ),
    }


def _assignment_records(serial: str, limit: int) -> list[dict[str, Any]]:
    return _query(
        f"""
        WITH latest_record AS
        (
            SELECT reel_frame_id,
                   argMax(
                       toString(source_package_id),
                       tuple(source_rank, toString(source_package_id))
                   ) AS package_id
            FROM markorbit_facts.us_assignment_record_history
            GROUP BY reel_frame_id
        ),
        linked AS
        (
            SELECT DISTINCT p.reel_frame_id
            FROM markorbit_facts.us_assignment_property_history AS p
            INNER JOIN latest_record AS lr
              ON p.reel_frame_id = lr.reel_frame_id
             AND toString(p.source_package_id) = lr.package_id
            WHERE p.serial_number = '{serial}'
        )
        SELECT r.*
        FROM markorbit_facts.us_assignment_record_history AS r
        INNER JOIN latest_record AS lr
          ON r.reel_frame_id = lr.reel_frame_id
         AND toString(r.source_package_id) = lr.package_id
        INNER JOIN linked AS l ON r.reel_frame_id = l.reel_frame_id
        ORDER BY r.recorded_date DESC NULLS LAST, r.source_rank DESC, r.reel_frame_id DESC
        LIMIT {int(limit)}
        """
    )


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _assignment_snapshot(serial: str, limit: int) -> dict[str, Any]:
    records = _assignment_records(serial, limit)
    current_owner_names = [
        str(row["party_name"])
        for row in _query(
            f"""
            SELECT party_name
            FROM markorbit_facts.us_owner_current FINAL
            WHERE serial_number = '{serial}'
              AND is_deleted = 0
              AND party_name != ''
            ORDER BY entry_number, party_name
            """
        )
    ]
    latest = records[0] if records else None
    latest_assignee_names: list[str] = []
    if latest is not None:
        reel_frame = str(latest["reel_frame_id"])
        package_id = str(latest["source_package_id"])
        latest_assignee_names = [
            str(row["party_name"])
            for row in _query(
                f"""
                SELECT party_name
                FROM markorbit_facts.us_assignment_assignee_history
                WHERE reel_frame_id = '{reel_frame}'
                  AND source_package_id = toUUID('{package_id}')
                  AND party_name != ''
                ORDER BY ordinal, party_name
                """
            )
        ]

    if not current_owner_names or not latest_assignee_names:
        comparison = "NOT_COMPARABLE"
    else:
        current_set = {_normalize_name(name) for name in current_owner_names}
        assignee_set = {_normalize_name(name) for name in latest_assignee_names}
        comparison = "MATCH" if current_set == assignee_set else "DIFFER"

    return {
        "schema_version": ASSIGNMENT_SCHEMA_VERSION,
        "assignment_count": len(records),
        "records": records,
        "latest_recorded_assignment": latest,
        "owner_name_reconciliation": {
            "comparison": comparison,
            "current_case_owner_names": current_owner_names,
            "latest_recorded_assignee_names": latest_assignee_names,
            "comparison_method": "WHITESPACE_AND_CASE_NORMALIZED_EXACT_NAME_SET_ONLY",
            "legal_ownership_conclusion": False,
        },
    }


def _maintenance_snapshot(application: dict[str, Any], as_of: date) -> dict[str, Any]:
    case = application["case"]
    registration_date = case.get("registration_date")
    if registration_date is None:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "NO_US_REGISTRATION_DATE",
            "semantics": MAINTENANCE_SEMANTICS,
        }
    if not isinstance(registration_date, date):
        raise TypeError("registration_date must be a date when present")
    madrid = bool(case.get("madrid_66a_current") or case.get("madrid_66a"))
    return calculate_maintenance_schedule(
        registration_date=registration_date,
        as_of=as_of,
        madrid_66a=madrid,
    )


def _domain(
    *,
    name: str,
    semantics: str,
    loader: Callable[[], Any],
) -> dict[str, Any]:
    try:
        data = loader()
    except Exception as exc:
        return {
            "name": name,
            "status": "NOT_AVAILABLE",
            "semantics": semantics,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {
        "name": name,
        "status": "AVAILABLE",
        "semantics": semantics,
        "data": data,
    }


def case_360_schema() -> dict[str, Any]:
    return {
        "view_version": CASE_360_VERSION,
        "semantics": CASE_360_SEMANTICS,
        "source_domains": [
            "uspto_application_facts",
            "durable_change_history",
            "uspto_recorded_assignments",
            "ttab_procedural_facts",
            "reviewed_deadline_evidence",
            "maintenance_deadline_metadata",
        ],
        "source_boundary_preserved": True,
        "partial_domain_failure_isolated": True,
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
        "ttab_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def build_case_360(
    serial_number: str,
    *,
    as_of: date | None = None,
    history_limit: int = 500,
    assignment_limit: int = 100,
    ttab_limit: int = 100,
) -> dict[str, Any] | None:
    serial = validate_serial_number(serial_number)
    if not 1 <= history_limit <= 5000:
        raise ValueError("history_limit must be between 1 and 5000")
    if not 1 <= assignment_limit <= 500:
        raise ValueError("assignment_limit must be between 1 and 500")
    if not 1 <= ttab_limit <= 500:
        raise ValueError("ttab_limit must be between 1 and 500")

    application = _application_snapshot(serial)
    if application is None:
        return None
    effective_as_of = as_of or date.today()

    domains = {
        "application": {
            "name": "application",
            "status": "AVAILABLE",
            "semantics": APPLICATION_SEMANTICS,
            "data": application,
        },
        "change_history": _domain(
            name="change_history",
            semantics=HISTORY_SEMANTICS,
            loader=lambda: build_case_timeline(serial, limit=history_limit),
        ),
        "assignment": _domain(
            name="assignment",
            semantics=ASSIGNMENT_SEMANTICS,
            loader=lambda: _assignment_snapshot(serial, assignment_limit),
        ),
        "ttab": _domain(
            name="ttab",
            semantics=TTAB_SEMANTICS,
            loader=lambda: {
                "schema_version": TTAB_SCHEMA_VERSION,
                "proceedings": proceedings_for_serial(serial, ttab_limit),
            },
        ),
        "deadline_evidence": _domain(
            name="deadline_evidence",
            semantics=DEADLINE_SEMANTICS,
            loader=lambda: resolve_case_deadline_evidence(
                serial_number=serial,
                raw_root=Path(get_settings().raw_data_root),
            ),
        ),
        "maintenance": _domain(
            name="maintenance",
            semantics=MAINTENANCE_SEMANTICS,
            loader=lambda: _maintenance_snapshot(application, effective_as_of),
        ),
    }
    coverage = {name: domain["status"] for name, domain in domains.items()}
    unavailable = [name for name, status in coverage.items() if status != "AVAILABLE"]
    warnings = [
        f"Domain {name} is unavailable; other source domains remain independently usable."
        for name in unavailable
    ]

    return {
        "view_version": CASE_360_VERSION,
        "serial_number": serial,
        "as_of": effective_as_of,
        "semantics": CASE_360_SEMANTICS,
        "coverage": coverage,
        "domains": domains,
        "warnings": warnings,
        "source_boundary_preserved": True,
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
        "ttab_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }
