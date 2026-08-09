from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from app.db import clickhouse_client
from app.us.event_roles import load_active_event_role_map


DEADLINE_EVIDENCE_RESOLVER_VERSION = "US_DEADLINE_EVENT_EVIDENCE_V1"
_OA_ROLES = {"OFFICE_ACTION_NONFINAL_ISSUED", "OFFICE_ACTION_FINAL_ISSUED"}
_OPPOSITION_EXTENSION_BY_ROLE = {
    "OPPOSITION_EXTENSION_30_GRANTED": 30,
    "OPPOSITION_EXTENSION_90_GRANTED": 90,
    "OPPOSITION_EXTENSION_150_GRANTED": 150,
}


def _date_value(value: object) -> date | None:
    if isinstance(value, date):
        return value
    return None


def _sequence(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _event_identity(event: dict[str, Any]) -> tuple[str, date | None, int, str]:
    return (
        str(event.get("event_code") or ""),
        _date_value(event.get("event_date")),
        _sequence(event.get("event_sequence")),
        str(event.get("event_type") or ""),
    )


def _event_sort_key(event: dict[str, Any]) -> tuple[date, int, str]:
    event_date = _date_value(event.get("event_date")) or date.min
    return (
        event_date,
        _sequence(event.get("event_sequence")),
        str(event.get("event_code") or ""),
    )


def _event_projection(event: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_code": str(event.get("event_code") or ""),
        "event_date": _date_value(event.get("event_date")),
        "event_sequence": _sequence(event.get("event_sequence")),
        "event_type": str(event.get("event_type") or ""),
        "description_text": str(event.get("description_text") or ""),
        "role": str(mapping["role"]),
        "rule_id": str(mapping["rule_id"]),
        "source_refs": list(mapping.get("source_refs") or []),
    }


def resolve_deadline_evidence(
    *,
    events: list[dict[str, Any]],
    role_state: dict[str, Any],
) -> dict[str, Any]:
    """Resolve conservative deadline inputs from a reviewed event-role mapping.

    Unknown event codes are never interpreted. A role ruleset must be active,
    source-evidenced, and bound to the active official USPTO event reference.
    """
    base: dict[str, Any] = {
        "resolver_version": DEADLINE_EVIDENCE_RESOLVER_VERSION,
        "automatic_inputs": {
            "office_action_issue_date": None,
            "office_action_final": None,
            "notice_of_allowance_date": None,
            "itu_extensions_granted": None,
            "statement_of_use_filed": None,
            "opposition_extension_days_granted": None,
        },
        "office_action": {"status": "NOT_RESOLVED"},
        "notice_of_allowance": {"status": "NOT_RESOLVED"},
        "opposition_extension": {"status": "NOT_RESOLVED"},
        "mapped_events": [],
        "unmapped_event_codes": [],
        "warnings": [],
        "semantics": "REVIEWED_EVENT_ROLE_EVIDENCE_NOT_APPLICATION_LEGAL_STATUS",
    }
    if role_state.get("status") != "PASS":
        return {
            **base,
            "status": "NOT_READY" if role_state.get("status") != "FAIL" else "FAIL",
            "reason": role_state.get("reason") or "event_role_mapping_not_ready",
            "role_ruleset": role_state.get("ruleset"),
        }

    role_map = role_state.get("roles") or {}
    deduped: dict[tuple[str, date | None, int, str], dict[str, Any]] = {}
    for event in events:
        deduped[_event_identity(event)] = event

    mapped: list[dict[str, Any]] = []
    unmapped_codes: set[str] = set()
    for event in deduped.values():
        code = str(event.get("event_code") or "").strip().upper()
        if not code:
            continue
        mapping = role_map.get(code)
        if mapping is None:
            unmapped_codes.add(code)
            continue
        if _date_value(event.get("event_date")) is None:
            base["warnings"].append(f"mapped_event_missing_date:{code}")
            continue
        mapped.append(_event_projection(event, mapping))
    mapped.sort(key=_event_sort_key)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in mapped:
        grouped.setdefault(str(event["role"]), []).append(event)

    # Office Action: use the latest mapped issuance only when no mapped response
    # occurred on/after that issuance. This avoids presenting a satisfied OA as pending.
    oa_issues = sorted(
        [event for event in mapped if event["role"] in _OA_ROLES],
        key=_event_sort_key,
    )
    if oa_issues:
        latest_oa = oa_issues[-1]
        issue_date = latest_oa["event_date"]
        responses = [
            event
            for event in grouped.get("OFFICE_ACTION_RESPONSE_FILED", [])
            if event["event_date"] >= issue_date
        ]
        if responses:
            base["office_action"] = {
                "status": "RESOLVED_BY_MAPPED_RESPONSE",
                "issue_event": latest_oa,
                "response_event": sorted(responses, key=_event_sort_key)[-1],
            }
        else:
            final = latest_oa["role"] == "OFFICE_ACTION_FINAL_ISSUED"
            base["office_action"] = {
                "status": "PENDING_CANDIDATE_FROM_REVIEWED_EVENT_ROLE",
                "issue_event": latest_oa,
                "final_action": final,
            }
            base["automatic_inputs"]["office_action_issue_date"] = issue_date
            base["automatic_inputs"]["office_action_final"] = final

    # NOA: multiple distinct NOA dates are deliberately ambiguous rather than
    # choosing one. This protects reinstatement/republication edge cases.
    noa_events = grouped.get("NOTICE_OF_ALLOWANCE_ISSUED", [])
    noa_dates = sorted({event["event_date"] for event in noa_events})
    if len(noa_dates) == 1:
        noa_date = noa_dates[0]
        extension_events = [
            event
            for event in grouped.get("ITU_EXTENSION_GRANTED", [])
            if event["event_date"] >= noa_date
        ]
        sou_events = [
            event
            for event in grouped.get("STATEMENT_OF_USE_FILED", [])
            if event["event_date"] >= noa_date
        ]
        if len(extension_events) > 5:
            base["notice_of_allowance"] = {
                "status": "AMBIGUOUS_EXTENSION_COUNT",
                "notice_of_allowance_date": noa_date,
                "extension_event_count": len(extension_events),
                "events": extension_events,
            }
            base["warnings"].append("mapped_itu_extension_count_exceeds_statutory_cap")
        else:
            base["notice_of_allowance"] = {
                "status": "RESOLVED_FROM_REVIEWED_EVENT_ROLES",
                "notice_of_allowance_date": noa_date,
                "extension_event_count": len(extension_events),
                "statement_of_use_event_count": len(sou_events),
                "noa_event": noa_events[0],
                "extension_events": extension_events,
                "statement_of_use_events": sou_events,
            }
            base["automatic_inputs"]["notice_of_allowance_date"] = noa_date
            base["automatic_inputs"]["itu_extensions_granted"] = len(extension_events)
            base["automatic_inputs"]["statement_of_use_filed"] = bool(sou_events)
    elif len(noa_dates) > 1:
        base["notice_of_allowance"] = {
            "status": "AMBIGUOUS_MULTIPLE_NOA_DATES",
            "dates": noa_dates,
            "events": noa_events,
        }
        base["warnings"].append("multiple_mapped_notice_of_allowance_dates")

    extension_totals = sorted(
        {
            _OPPOSITION_EXTENSION_BY_ROLE[event["role"]]
            for event in mapped
            if event["role"] in _OPPOSITION_EXTENSION_BY_ROLE
        }
    )
    if extension_totals:
        total = max(extension_totals)
        base["opposition_extension"] = {
            "status": "RESOLVED_FROM_REVIEWED_EVENT_ROLES",
            "total_extension_days_granted": total,
            "observed_extension_totals": extension_totals,
            "events": [
                event
                for event in mapped
                if event["role"] in _OPPOSITION_EXTENSION_BY_ROLE
            ],
        }
        base["automatic_inputs"]["opposition_extension_days_granted"] = total

    return {
        **base,
        "status": "PASS",
        "reason": None,
        "role_ruleset": role_state.get("ruleset"),
        "mapped_events": mapped,
        "unmapped_event_codes": sorted(unmapped_codes),
    }


def case_events(serial_number: str) -> list[dict[str, Any]]:
    if len(serial_number) != 8 or not serial_number.isdigit():
        raise ValueError("serial_number must contain exactly 8 digits")
    result = clickhouse_client().query(
        f"""
        SELECT event_code, event_date, event_sequence,
               event_type_code AS event_type, description_text
        FROM markorbit_facts.us_event_history FINAL
        WHERE serial_number = '{serial_number}'
        ORDER BY event_date, event_sequence, event_code
        """
    )
    return [
        dict(zip(result.column_names, row, strict=True))
        for row in result.result_rows
    ]


def resolve_case_deadline_evidence(
    *,
    serial_number: str,
    raw_root: Path,
) -> dict[str, Any]:
    return resolve_deadline_evidence(
        events=case_events(serial_number),
        role_state=load_active_event_role_map(raw_root),
    )
