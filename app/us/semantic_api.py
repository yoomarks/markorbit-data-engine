from __future__ import annotations

from datetime import date
import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.db import clickhouse_client
from app.us.application_deadlines import (
    APPLICATION_DEADLINE_RULE_VERIFIED_ON,
    APPLICATION_DEADLINE_RULE_VERSION,
    EVIDENCE_REFS as APPLICATION_DEADLINE_EVIDENCE_REFS,
    SHORTENED_OA_CUTOFF,
    calculate_application_deadlines,
)
from app.us.event_reference import lookup_active_event_codes
from app.us.event_reference_inventory import build_inventory as build_event_inventory
from app.us.maintenance import (
    EVIDENCE_REFS,
    MAINTENANCE_RULE_VERIFIED_ON,
    MAINTENANCE_RULE_VERSION,
    MODERN_TERM_CUTOFF,
    calculate_maintenance_schedule,
)
from app.us.reference_acceptance import build_reference_acceptance
from app.us.reference_evidence import verify_source_evidence
from app.us.semantic_readiness import build_semantic_readiness
from app.us.status_interpretation import active_ruleset, interpret_status
from app.us.status_reference import lookup_active_status_codes
from app.us.status_reference_inventory import build_inventory as build_status_inventory


router = APIRouter(prefix="/api/us", tags=["US semantic"])
_EVENT_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,63}$")


def _strict_serial(serial_number: str) -> str:
    serial = serial_number.strip()
    if len(serial) != 8 or not serial.isdigit():
        raise HTTPException(
            status_code=400,
            detail="USPTO serial number must contain exactly 8 digits",
        )
    return serial


def _query_dicts(sql: str) -> list[dict[str, Any]]:
    try:
        result = clickhouse_client().query(sql)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_DATASTORE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
    return [
        dict(zip(result.column_names, row, strict=True))
        for row in result.result_rows
    ]


def _case_semantic_facts(serial: str) -> dict[str, Any]:
    rows = _query_dicts(
        f"""
        SELECT
            serial_number,
            registration_number,
            filing_date,
            publication_date,
            registration_date,
            status_code,
            status_date,
            intent_to_use_1b,
            intent_to_use_1b_filed,
            intent_to_use_1b_current,
            madrid_66a,
            madrid_66a_filed,
            madrid_66a_current,
            international_registration_number,
            international_registration_date,
            international_renewal_date,
            renewal_date,
            section_8_filed,
            section_8_accepted,
            section_15_filed,
            section_15_acknowledged
        FROM markorbit_facts.us_case_current FINAL
        WHERE serial_number = '{serial}' AND is_deleted = 0
        LIMIT 1
        """
    )
    if not rows:
        raise HTTPException(status_code=404, detail="US trademark case not found")
    return rows[0]


def _case_event_codes(serial: str) -> list[str]:
    rows = _query_dicts(
        f"""
        SELECT DISTINCT event_code
        FROM markorbit_facts.us_event_history FINAL
        WHERE serial_number = '{serial}' AND event_code != ''
        ORDER BY event_code
        """
    )
    return [str(row["event_code"]) for row in rows]


@router.get("/references/status")
def us_status_reference_inventory():
    return build_status_inventory()


@router.get("/references/status/{status_code}")
def us_status_reference_lookup(status_code: str):
    code = status_code.strip()
    if not code.isdigit() or len(code) > 10:
        raise HTTPException(status_code=400, detail="USPTO status code must be numeric")
    lookup = lookup_active_status_codes([code])
    return {
        "status_code": code,
        "reference": lookup["reference"],
        "official_reference": lookup["mappings"].get(code),
        "mapped": code in lookup["mappings"],
        "semantics": "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION",
    }


@router.get("/references/events")
def us_event_reference_inventory():
    return build_event_inventory()


@router.get("/references/events/{event_code}")
def us_event_reference_lookup(event_code: str):
    code = event_code.strip().upper()
    if not _EVENT_CODE_RE.fullmatch(code):
        raise HTTPException(status_code=400, detail="USPTO event code has an invalid format")
    lookup = lookup_active_event_codes([code])
    return {
        "event_code": code,
        "reference": lookup["reference"],
        "official_reference": lookup["mappings"].get(code),
        "mapped": code in lookup["mappings"],
        "semantics": "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION",
    }


@router.get("/references/acceptance")
def us_reference_acceptance():
    return build_reference_acceptance(get_settings().raw_data_root)


@router.get("/semantic-readiness")
def us_semantic_readiness(
    expected_history_parts: int = Query(..., ge=1),
    deep_source_test: bool = False,
):
    return build_semantic_readiness(
        get_settings().raw_data_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )


@router.get("/interpretation/ruleset")
def us_active_interpretation_ruleset():
    try:
        ruleset = active_ruleset()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_INTERPRETATION_SCHEMA_NOT_READY",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
    if ruleset is None:
        return {
            "ruleset": None,
            "evidence": None,
            "semantics": "NO_ACTIVE_RULESET_INTERPRETATION_RETURNS_UNKNOWN",
        }
    return {
        "ruleset": ruleset,
        "evidence": verify_source_evidence(
            ruleset,
            get_settings().raw_data_root,
            family="interpretation",
        ),
        "semantics": "MARKORBIT_DERIVED_RULESET_NOT_USPTO_OFFICIAL_FACT",
    }


@router.get("/status-interpretation/{serial_number}")
def us_status_interpretation(serial_number: str):
    serial = _strict_serial(serial_number)
    case = _case_semantic_facts(serial)
    event_codes = _case_event_codes(serial)
    raw_status_code = str(case.get("status_code") or "")
    status_lookup = lookup_active_status_codes([raw_status_code])
    event_lookup = lookup_active_event_codes(event_codes)
    try:
        derived = interpret_status(
            raw_root=get_settings().raw_data_root,
            status_code=raw_status_code,
            event_codes=event_codes,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_INTERPRETATION_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc

    return {
        "serial_number": serial,
        "raw_uspto_fact": {
            "status_code": raw_status_code,
            "status_date": case.get("status_date"),
            "event_codes": event_codes,
        },
        "official_reference": {
            "status_reference_version": (
                status_lookup["reference"].get("reference_version")
                if status_lookup["reference"]
                else None
            ),
            "status": status_lookup["mappings"].get(raw_status_code),
            "event_reference_version": (
                event_lookup["reference"].get("reference_version")
                if event_lookup["reference"]
                else None
            ),
            "events": event_lookup["mappings"],
        },
        "markorbit_derived_interpretation": derived,
        "semantics": "RAW_FACT_OFFICIAL_REFERENCE_AND_DERIVED_INTERPRETATION_ARE_SEPARATE_LAYERS",
    }


@router.get("/maintenance/rules")
def us_maintenance_rule_metadata():
    return {
        "rule_version": MAINTENANCE_RULE_VERSION,
        "rule_verified_on": MAINTENANCE_RULE_VERIFIED_ON,
        "modern_term_cutoff": MODERN_TERM_CUTOFF,
        "semantics": "DEADLINE_CALCULATION_NOT_CASE_LEGAL_STATUS",
        "evidence_refs": list(EVIDENCE_REFS),
        "business_day_adjustment": "NOT_CALCULATED_CHECK_USPTO",
        "production_legal_status_inference": False,
    }


@router.get("/maintenance/{serial_number}")
def us_maintenance_schedule(
    serial_number: str,
    as_of: date | None = None,
    current_term_expiration_date: date | None = None,
):
    serial = _strict_serial(serial_number)
    case = _case_semantic_facts(serial)
    registration_date = case.get("registration_date")
    if registration_date is None:
        return {
            "serial_number": serial,
            "status": "NOT_READY",
            "reason": "registration_date_missing",
            "semantics": "DEADLINE_CALCULATION_NOT_CASE_LEGAL_STATUS",
        }
    madrid = bool(case.get("madrid_66a_current") or case.get("madrid_66a"))
    try:
        schedule = calculate_maintenance_schedule(
            registration_date=registration_date,
            as_of=as_of or date.today(),
            madrid_66a=madrid,
            international_registration_date=case.get("international_registration_date"),
            current_term_expiration_date=current_term_expiration_date,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_MAINTENANCE_INPUT_INVALID", "error": str(exc)},
        ) from exc
    return {
        "serial_number": serial,
        "registration_number": case.get("registration_number"),
        "source_case_flags": {
            "madrid_66a": madrid,
            "renewal_date": case.get("renewal_date"),
            "section_8_filed": case.get("section_8_filed"),
            "section_8_accepted": case.get("section_8_accepted"),
            "section_15_filed": case.get("section_15_filed"),
            "section_15_acknowledged": case.get("section_15_acknowledged"),
        },
        "schedule": schedule,
    }


@router.get("/application-deadlines/rules")
def us_application_deadline_rule_metadata():
    return {
        "rule_version": APPLICATION_DEADLINE_RULE_VERSION,
        "rule_verified_on": APPLICATION_DEADLINE_RULE_VERIFIED_ON,
        "shortened_oa_cutoff": SHORTENED_OA_CUTOFF,
        "semantics": "DEADLINE_METADATA_ONLY_NO_APPLICATION_LEGAL_STATUS",
        "evidence_refs": list(APPLICATION_DEADLINE_EVIDENCE_REFS),
        "business_day_adjustment": "NOT_CALCULATED_CHECK_OFFICIAL_NOTICE_USPTO_OR_TTAB",
        "automatic_event_code_inference": False,
    }


@router.get("/application-deadlines/{serial_number}")
def us_application_deadlines(
    serial_number: str,
    as_of: date | None = None,
    office_action_issue_date: date | None = None,
    office_action_final: bool = False,
    office_action_notice_deadline: date | None = None,
    notice_of_allowance_date: date | None = None,
    itu_extensions_granted: Annotated[int | None, Query(ge=0, le=5)] = None,
    statement_of_use_filed: bool = False,
    opposition_extension_days_granted: int | None = None,
):
    serial = _strict_serial(serial_number)
    case = _case_semantic_facts(serial)
    madrid = bool(case.get("madrid_66a_current") or case.get("madrid_66a"))
    publication_date = case.get("publication_date")
    try:
        schedule = calculate_application_deadlines(
            as_of=as_of or date.today(),
            madrid_66a=madrid,
            publication_date=publication_date,
            office_action_issue_date=office_action_issue_date,
            office_action_final=office_action_final,
            office_action_notice_deadline=office_action_notice_deadline,
            notice_of_allowance_date=notice_of_allowance_date,
            itu_extensions_granted=itu_extensions_granted,
            statement_of_use_filed=statement_of_use_filed,
            opposition_extension_days_granted=opposition_extension_days_granted,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_APPLICATION_DEADLINE_INPUT_INVALID", "error": str(exc)},
        ) from exc

    warnings: list[str] = []
    if notice_of_allowance_date is not None and not bool(
        case.get("intent_to_use_1b_filed") or case.get("intent_to_use_1b")
    ):
        warnings.append(
            "A Notice of Allowance date was supplied but the current case facts do not show a Section 1(b) filed-basis flag; verify the USPTO record."
        )
    return {
        "serial_number": serial,
        "source_case_facts": {
            "filing_date": case.get("filing_date"),
            "publication_date": publication_date,
            "intent_to_use_1b_filed": bool(
                case.get("intent_to_use_1b_filed") or case.get("intent_to_use_1b")
            ),
            "intent_to_use_1b_current": bool(case.get("intent_to_use_1b_current")),
            "madrid_66a": madrid,
        },
        "explicit_evidence_inputs": {
            "office_action_issue_date": office_action_issue_date,
            "office_action_notice_deadline": office_action_notice_deadline,
            "notice_of_allowance_date": notice_of_allowance_date,
            "itu_extensions_granted": itu_extensions_granted,
            "statement_of_use_filed": statement_of_use_filed,
            "opposition_extension_days_granted": opposition_extension_days_granted,
        },
        "schedule": schedule,
        "warnings": warnings,
    }
