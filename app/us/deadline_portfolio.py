from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from app.db import clickhouse_client
from app.us.application_deadlines import (
    calculate_itu_noa_schedule,
    calculate_office_action_deadline,
    calculate_publication_opposition_schedule,
)
from app.us.deadline_evidence import resolve_deadline_evidence
from app.us.event_roles import load_active_event_role_map
from app.us.maintenance import calculate_maintenance_schedule


DEADLINE_PORTFOLIO_VERSION = "US_DEADLINE_PORTFOLIO_V1"


def _as_date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _urgency(
    due: date,
    *,
    as_of: date,
    horizon_days: int,
    recent_past_days: int,
) -> str | None:
    delta = (due - as_of).days
    if delta < -recent_past_days or delta > horizon_days:
        return None
    if delta < 0:
        return "RECENT_PAST_NOMINAL_DEADLINE"
    if delta == 0:
        return "DUE_TODAY"
    if delta <= 7:
        return "DUE_WITHIN_7_DAYS"
    if delta <= 30:
        return "DUE_WITHIN_30_DAYS"
    if delta <= 90:
        return "DUE_WITHIN_90_DAYS"
    return "DUE_WITHIN_HORIZON"


def _candidate(
    *,
    serial_number: str,
    registration_number: str,
    family: str,
    code: str,
    label: str,
    due_date: date,
    urgency: str,
    source: str,
    state: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_id": f"{serial_number}:{family}:{code}:{due_date.isoformat()}",
        "serial_number": serial_number,
        "registration_number": registration_number,
        "family": family,
        "code": code,
        "label": label,
        "due_date": due_date,
        "urgency": urgency,
        "state": state,
        "source": source,
        "details": details,
        "legal_status_inference": False,
    }


def build_case_deadline_candidates(
    *,
    case: dict[str, Any],
    events: list[dict[str, Any]],
    role_state: dict[str, Any],
    as_of: date,
    horizon_days: int = 90,
    recent_past_days: int = 30,
) -> list[dict[str, Any]]:
    if horizon_days < 0 or recent_past_days < 0:
        raise ValueError("horizon_days and recent_past_days must be non-negative")
    serial = str(case.get("serial_number") or "")
    registration_number = str(case.get("registration_number") or "")
    madrid = bool(case.get("madrid_66a_current") or case.get("madrid_66a"))
    candidates: list[dict[str, Any]] = []

    registration_date = _as_date(case.get("registration_date"))
    if registration_date is not None and registration_date <= as_of:
        maintenance = calculate_maintenance_schedule(
            registration_date=registration_date,
            as_of=as_of,
            madrid_66a=madrid,
            international_registration_date=_as_date(
                case.get("international_registration_date")
            ),
        )
        for obligation in maintenance["obligations"]:
            state = str(obligation["state_as_of"])
            # Once the regular period has passed, the grace deadline is the useful
            # operational boundary, including a recently elapsed grace period.
            if state in {"OPEN_GRACE", "PAST_DEADLINE"}:
                due = obligation["nominal_grace_deadline"]
            else:
                due = obligation["nominal_regular_deadline"]
            urgency = _urgency(
                due,
                as_of=as_of,
                horizon_days=horizon_days,
                recent_past_days=recent_past_days,
            )
            if urgency is None:
                continue
            candidates.append(
                _candidate(
                    serial_number=serial,
                    registration_number=registration_number,
                    family="MAINTENANCE",
                    code=str(obligation["code"]),
                    label=str(obligation["label"]),
                    due_date=due,
                    urgency=urgency,
                    source="OFFICIAL_CASE_REGISTRATION_DATE_PLUS_VERSIONED_RULE",
                    state=state,
                    details={
                        "rule_version": maintenance["rule_version"],
                        "regular_deadline": obligation["nominal_regular_deadline"],
                        "grace_deadline": obligation["nominal_grace_deadline"],
                        "business_day_adjustment": obligation[
                            "business_day_adjustment"
                        ],
                    },
                )
            )

    event_evidence = resolve_deadline_evidence(events=events, role_state=role_state)
    automatic = event_evidence["automatic_inputs"]

    publication_date = _as_date(case.get("publication_date"))
    if publication_date is not None and publication_date <= as_of:
        # Opposition-extension events belong to a publication cycle. Do not reuse a
        # reviewed extension event that predates the current official publication.
        publication_events = [
            event
            for event in events
            if (_as_date(event.get("event_date")) or date.min) >= publication_date
        ]
        publication_evidence = resolve_deadline_evidence(
            events=publication_events,
            role_state=role_state,
        )
        opposition_extension_days = publication_evidence["automatic_inputs"][
            "opposition_extension_days_granted"
        ]
        opposition = calculate_publication_opposition_schedule(
            publication_date=publication_date,
            as_of=as_of,
            extension_days_granted=opposition_extension_days,
        )
        assessment = opposition["current_deadline_assessment"]
        if assessment["assessment"] == "DEADLINE_FROM_EXPLICIT_EXTENSION_GRANT_FACT":
            due = assessment["nominal_opposition_deadline"]
            source = "OFFICIAL_PUBLICATION_DATE_PLUS_REVIEWED_EVENT_ROLE"
            state = assessment["state_as_of"]
        else:
            due = opposition["original_opposition_deadline"]
            source = "OFFICIAL_PUBLICATION_DATE_ORIGINAL_30_DAY_PERIOD"
            state = opposition["original_deadline_state"]
        urgency = _urgency(
            due,
            as_of=as_of,
            horizon_days=horizon_days,
            recent_past_days=recent_past_days,
        )
        if urgency is not None:
            candidates.append(
                _candidate(
                    serial_number=serial,
                    registration_number=registration_number,
                    family="PUBLICATION",
                    code="OPPOSITION_PERIOD",
                    label="Publication opposition period",
                    due_date=due,
                    urgency=urgency,
                    source=source,
                    state=state,
                    details={
                        "publication_date": publication_date,
                        "extension_days_granted": opposition_extension_days,
                        "extension_facts_known": opposition_extension_days is not None,
                        "business_day_adjustment": "NOT_CALCULATED_CHECK_TTAB",
                    },
                )
            )

    oa_issue_date = automatic["office_action_issue_date"]
    if isinstance(oa_issue_date, date) and oa_issue_date <= as_of:
        oa = calculate_office_action_deadline(
            issue_date=oa_issue_date,
            as_of=as_of,
            madrid_66a=madrid,
            final_action=bool(automatic["office_action_final"]),
        )
        due = oa["operational_deadline"]
        urgency = _urgency(
            due,
            as_of=as_of,
            horizon_days=horizon_days,
            recent_past_days=recent_past_days,
        )
        if urgency is not None:
            candidates.append(
                _candidate(
                    serial_number=serial,
                    registration_number=registration_number,
                    family="APPLICATION",
                    code="FINAL_OFFICE_ACTION_RESPONSE"
                    if automatic["office_action_final"]
                    else "NONFINAL_OFFICE_ACTION_RESPONSE",
                    label="Office Action response candidate",
                    due_date=due,
                    urgency=urgency,
                    source="REVIEWED_EVENT_ROLE_PLUS_VERSIONED_OA_RULE",
                    state=str(oa["operational_deadline_state"]),
                    details={
                        "event_role_ruleset": (
                            event_evidence.get("role_ruleset") or {}
                        ).get("ruleset_version"),
                        "issue_date": oa_issue_date,
                        "standard_deadline_if_extension_granted": oa.get(
                            "standard_deadline_if_extension_granted"
                        ),
                        "extension_grant_not_inferred": True,
                    },
                )
            )

    noa_date = automatic["notice_of_allowance_date"]
    extensions_granted = automatic["itu_extensions_granted"]
    sou_filed = automatic["statement_of_use_filed"]
    if (
        isinstance(noa_date, date)
        and noa_date <= as_of
        and isinstance(extensions_granted, int)
        and sou_filed is False
    ):
        noa = calculate_itu_noa_schedule(
            notice_of_allowance_date=noa_date,
            as_of=as_of,
            extensions_granted=extensions_granted,
            statement_of_use_filed=False,
        )
        current = noa["current_deadline_assessment"]
        due = current.get("nominal_deadline")
        if isinstance(due, date):
            urgency = _urgency(
                due,
                as_of=as_of,
                horizon_days=horizon_days,
                recent_past_days=recent_past_days,
            )
            if urgency is not None:
                candidates.append(
                    _candidate(
                        serial_number=serial,
                        registration_number=registration_number,
                        family="APPLICATION",
                        code="ITU_SOU_OR_EXTENSION",
                        label="Section 1(b) SOU / extension candidate",
                        due_date=due,
                        urgency=urgency,
                        source="REVIEWED_EVENT_ROLE_PLUS_VERSIONED_NOA_RULE",
                        state=str(current["state_as_of"]),
                        details={
                            "notice_of_allowance_date": noa_date,
                            "extensions_granted": extensions_granted,
                            "required_action": current["required_action"],
                            "event_role_ruleset": (
                                event_evidence.get("role_ruleset") or {}
                            ).get("ruleset_version"),
                        },
                    )
                )

    candidates.sort(
        key=lambda item: (item["due_date"], item["serial_number"], item["family"], item["code"])
    )
    return candidates


def _query_case_page(after_serial: str, scan_limit: int) -> list[dict[str, Any]]:
    if after_serial and (len(after_serial) != 8 or not after_serial.isdigit()):
        raise ValueError("after_serial must be empty or exactly 8 digits")
    safe_after = after_serial or "00000000"
    result = clickhouse_client().query(
        f"""
        SELECT
            serial_number,
            registration_number,
            publication_date,
            registration_date,
            madrid_66a,
            madrid_66a_current,
            international_registration_date
        FROM markorbit_facts.us_case_current FINAL
        WHERE is_deleted = 0
          AND serial_number > '{safe_after}'
        ORDER BY serial_number
        LIMIT {int(scan_limit)}
        """
    )
    return [
        dict(zip(result.column_names, row, strict=True))
        for row in result.result_rows
    ]


def _query_events_for_serials(serials: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not serials:
        return {}
    literals = ",".join(f"'{serial}'" for serial in serials)
    result = clickhouse_client().query(
        f"""
        SELECT serial_number, event_code, event_date, event_sequence,
               event_type_code AS event_type, description_text
        FROM markorbit_facts.us_event_history FINAL
        WHERE serial_number IN ({literals})
        ORDER BY serial_number, event_date, event_sequence, event_code
        """
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result.result_rows:
        item = dict(zip(result.column_names, row, strict=True))
        grouped[str(item["serial_number"])].append(item)
    return dict(grouped)


def scan_deadline_candidate_page(
    *,
    raw_root: Path,
    as_of: date,
    after_serial: str = "",
    scan_limit: int = 200,
    result_limit: int = 500,
    horizon_days: int = 90,
    recent_past_days: int = 30,
) -> dict[str, Any]:
    if not 1 <= scan_limit <= 2000:
        raise ValueError("scan_limit must be between 1 and 2000")
    if not 1 <= result_limit <= 5000:
        raise ValueError("result_limit must be between 1 and 5000")
    if not 0 <= horizon_days <= 3650:
        raise ValueError("horizon_days must be between 0 and 3650")
    if not 0 <= recent_past_days <= 365:
        raise ValueError("recent_past_days must be between 0 and 365")

    cases = _query_case_page(after_serial, scan_limit)
    role_state = load_active_event_role_map(raw_root)
    serials = [str(case["serial_number"]) for case in cases]
    events_by_serial = (
        _query_events_for_serials(serials) if role_state.get("status") == "PASS" else {}
    )
    candidates: list[dict[str, Any]] = []
    for case in cases:
        serial = str(case["serial_number"])
        candidates.extend(
            build_case_deadline_candidates(
                case=case,
                events=events_by_serial.get(serial, []),
                role_state=role_state,
                as_of=as_of,
                horizon_days=horizon_days,
                recent_past_days=recent_past_days,
            )
        )

    candidates.sort(
        key=lambda item: (item["due_date"], item["serial_number"], item["family"], item["code"])
    )
    truncated = len(candidates) > result_limit
    candidates = candidates[:result_limit]
    last_scanned_serial = serials[-1] if serials else after_serial
    return {
        "version": DEADLINE_PORTFOLIO_VERSION,
        "as_of": as_of,
        "horizon_days": horizon_days,
        "recent_past_days": recent_past_days,
        "after_serial": after_serial,
        "scanned_case_count": len(cases),
        "last_scanned_serial": last_scanned_serial,
        "has_more_cases": len(cases) == scan_limit,
        "candidate_count": len(candidates),
        "result_truncated": truncated,
        "event_role_state": {
            "status": role_state.get("status"),
            "reason": role_state.get("reason"),
            "ruleset_version": (role_state.get("ruleset") or {}).get(
                "ruleset_version"
            ),
        },
        "candidates": candidates,
        "semantics": "BOUNDED_DEADLINE_CANDIDATES_NOT_LEGAL_STATUS_OR_FINAL_DOCKET",
    }
