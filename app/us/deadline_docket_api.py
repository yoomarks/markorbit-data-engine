from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.db import clickhouse_client
from app.us.application_deadlines import calculate_application_deadlines
from app.us.deadline_evidence import resolve_case_deadline_evidence
from app.us.deadline_portfolio import scan_deadline_candidate_page
from app.us.event_roles import load_active_event_role_map


router = APIRouter(prefix="/api/us", tags=["US deadline docket"])


def _strict_serial(serial_number: str) -> str:
    serial = serial_number.strip()
    if len(serial) != 8 or not serial.isdigit():
        raise HTTPException(
            status_code=400,
            detail="USPTO serial number must contain exactly 8 digits",
        )
    return serial


def _query_case(serial: str) -> dict[str, Any]:
    try:
        result = clickhouse_client().query(
            f"""
            SELECT
                serial_number,
                registration_number,
                filing_date,
                publication_date,
                registration_date,
                intent_to_use_1b,
                intent_to_use_1b_filed,
                intent_to_use_1b_current,
                madrid_66a,
                madrid_66a_current
            FROM markorbit_facts.us_case_current FINAL
            WHERE serial_number = '{serial}' AND is_deleted = 0
            LIMIT 1
            """
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_DATASTORE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
    if not result.result_rows:
        raise HTTPException(status_code=404, detail="US trademark case not found")
    return dict(zip(result.column_names, result.result_rows[0], strict=True))


def _source(explicit: object, automatic: object, *, case_fact: bool = False) -> str:
    if explicit is not None:
        return "EXPLICIT_API_EVIDENCE"
    if case_fact:
        return "OFFICIAL_USPTO_CASE_FACT"
    if automatic is not None:
        return "REVIEWED_EVENT_ROLE_EVIDENCE"
    return "MISSING"


@router.get("/event-roles/ruleset")
def us_event_role_ruleset():
    state = load_active_event_role_map(get_settings().raw_data_root)
    return {
        "status": state.get("status"),
        "reason": state.get("reason"),
        "ruleset": state.get("ruleset"),
        "official_event_reference": state.get("official_event_reference"),
        "ruleset_evidence": state.get("ruleset_evidence"),
        "reference_evidence": state.get("reference_evidence"),
        "role_count": len(state.get("roles") or {}),
        "roles": state.get("roles") or {},
        "semantics": "REVIEWED_EVENT_ROLE_MAPPING_NOT_USPTO_RAW_FACT",
    }


@router.get("/deadline-evidence/{serial_number}")
def us_deadline_evidence(serial_number: str):
    serial = _strict_serial(serial_number)
    _query_case(serial)
    try:
        return resolve_case_deadline_evidence(
            serial_number=serial,
            raw_root=get_settings().raw_data_root,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_DEADLINE_EVIDENCE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc


@router.get("/application-deadlines-resolved/{serial_number}")
def us_application_deadlines_resolved(
    serial_number: str,
    as_of: date | None = None,
    office_action_issue_date: date | None = None,
    office_action_final: bool | None = None,
    office_action_notice_deadline: date | None = None,
    notice_of_allowance_date: date | None = None,
    itu_extensions_granted: Annotated[int | None, Query(ge=0, le=5)] = None,
    statement_of_use_filed: bool | None = None,
    opposition_extension_days_granted: int | None = None,
):
    serial = _strict_serial(serial_number)
    case = _query_case(serial)
    try:
        event_evidence = resolve_case_deadline_evidence(
            serial_number=serial,
            raw_root=get_settings().raw_data_root,
        )
    except Exception as exc:
        event_evidence = {
            "status": "NOT_READY",
            "reason": "event_evidence_resolution_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "automatic_inputs": {},
        }
    automatic = event_evidence.get("automatic_inputs") or {}

    effective_oa_date = office_action_issue_date or automatic.get(
        "office_action_issue_date"
    )
    effective_oa_final = (
        office_action_final
        if office_action_final is not None
        else automatic.get("office_action_final")
    )
    effective_noa_date = notice_of_allowance_date or automatic.get(
        "notice_of_allowance_date"
    )
    effective_extensions = (
        itu_extensions_granted
        if itu_extensions_granted is not None
        else automatic.get("itu_extensions_granted")
    )
    effective_sou = (
        statement_of_use_filed
        if statement_of_use_filed is not None
        else automatic.get("statement_of_use_filed")
    )
    effective_opposition_extension = (
        opposition_extension_days_granted
        if opposition_extension_days_granted is not None
        else automatic.get("opposition_extension_days_granted")
    )
    madrid = bool(case.get("madrid_66a_current") or case.get("madrid_66a"))
    publication_date = case.get("publication_date")

    try:
        schedule = calculate_application_deadlines(
            as_of=as_of or date.today(),
            madrid_66a=madrid,
            publication_date=publication_date,
            office_action_issue_date=effective_oa_date,
            office_action_final=bool(effective_oa_final),
            office_action_notice_deadline=office_action_notice_deadline,
            notice_of_allowance_date=effective_noa_date,
            itu_extensions_granted=effective_extensions,
            statement_of_use_filed=bool(effective_sou),
            opposition_extension_days_granted=effective_opposition_extension,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_APPLICATION_DEADLINE_INPUT_INVALID", "error": str(exc)},
        ) from exc

    return {
        "serial_number": serial,
        "source_case_facts": {
            "publication_date": publication_date,
            "intent_to_use_1b_filed": bool(
                case.get("intent_to_use_1b_filed") or case.get("intent_to_use_1b")
            ),
            "intent_to_use_1b_current": bool(case.get("intent_to_use_1b_current")),
            "madrid_66a": madrid,
        },
        "input_provenance": {
            "publication_date": "OFFICIAL_USPTO_CASE_FACT"
            if publication_date is not None
            else "MISSING",
            "office_action_issue_date": _source(
                office_action_issue_date,
                automatic.get("office_action_issue_date"),
            ),
            "office_action_final": _source(
                office_action_final,
                automatic.get("office_action_final"),
            ),
            "office_action_notice_deadline": _source(
                office_action_notice_deadline,
                None,
            ),
            "notice_of_allowance_date": _source(
                notice_of_allowance_date,
                automatic.get("notice_of_allowance_date"),
            ),
            "itu_extensions_granted": _source(
                itu_extensions_granted,
                automatic.get("itu_extensions_granted"),
            ),
            "statement_of_use_filed": _source(
                statement_of_use_filed,
                automatic.get("statement_of_use_filed"),
            ),
            "opposition_extension_days_granted": _source(
                opposition_extension_days_granted,
                automatic.get("opposition_extension_days_granted"),
            ),
        },
        "event_evidence": event_evidence,
        "schedule": schedule,
        "semantics": "EXPLICIT_OR_REVIEWED_EVENT_EVIDENCE_DEADLINES_NOT_APPLICATION_LEGAL_STATUS",
    }


@router.get("/deadlines/candidates")
def us_deadline_candidate_page(
    as_of: date | None = None,
    after_serial: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=500)] = 200,
    horizon_days: Annotated[int, Query(ge=0, le=3650)] = 90,
    recent_past_days: Annotated[int, Query(ge=0, le=365)] = 30,
):
    try:
        report = scan_deadline_candidate_page(
            raw_root=Path(get_settings().raw_data_root),
            as_of=as_of or date.today(),
            after_serial=after_serial,
            scan_limit=scan_limit,
            result_limit=5000,
            horizon_days=horizon_days,
            recent_past_days=recent_past_days,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_DEADLINE_CANDIDATE_INPUT_INVALID", "error": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_DEADLINE_CANDIDATE_SCAN_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
    if report["result_truncated"]:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_DEADLINE_CANDIDATE_BUFFER_EXCEEDED",
                "instruction": "Retry with a lower scan_limit to preserve lossless pagination.",
            },
        )
    return report
