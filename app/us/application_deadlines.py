from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.us.maintenance import add_months


APPLICATION_DEADLINE_RULE_VERSION = "US_APPLICATION_DEADLINES_2026_08_09_V1"
APPLICATION_DEADLINE_RULE_VERIFIED_ON = date(2026, 8, 9)
SHORTENED_OA_CUTOFF = date(2022, 12, 3)

EVIDENCE_REFS: tuple[dict[str, str], ...] = (
    {
        "id": "USPTO_RESPONSE_TIME_PERIOD",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/apply/response-time-period",
        "supports": "Standard pre-registration Office Action response periods and Madrid exception",
    },
    {
        "id": "USPTO_RESPONSE_FORMS",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/apply/response-forms",
        "supports": "Three-month response, paid three-month extension, Section 66(a) six-month response, business-day reminder",
    },
    {
        "id": "TMEP_711",
        "authority": "USPTO_TMEP",
        "url": "https://tmep.uspto.gov/RDMS/TMEP/print?href=TMEP-700d1e1.html&version=current",
        "supports": "Section 1/44 three-month response plus one extension; Section 66(a) six-month response",
    },
    {
        "id": "USPTO_ITU_FORMS",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/apply/intent-use-itu-forms",
        "supports": "Notice of Allowance six-month SOU/extension periods, five extension requests, three-year maximum",
    },
    {
        "id": "USPTO_SOU_MINIMUM",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/apply/statement-use-sou-minimum-filing-requirements",
        "supports": "Statement of Use timeliness measured from Notice of Allowance or granted extension",
    },
    {
        "id": "USPTO_TTAB_EXTENSIONS",
        "authority": "USPTO_TTAB",
        "url": "https://www.uspto.gov/trademarks/ttab/index-estta-forms",
        "supports": "Thirty-day opposition period and 30/90/additional 60/final 60 extension structure",
    },
    {
        "id": "USPTO_TRADEMARK_PROCESS",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/basics/trademark-process",
        "supports": "Application process and thirty-day publication opposition period",
    },
)


def _state(as_of: date, deadline: date) -> str:
    if as_of < deadline:
        return "FUTURE"
    if as_of == deadline:
        return "DUE_NOMINAL_DATE"
    return "PAST_NOMINAL_DATE"


def _deadline_metadata(deadline: date, as_of: date) -> dict[str, Any]:
    return {
        "nominal_deadline": deadline,
        "state_as_of": _state(as_of, deadline),
        "falls_on_weekend": deadline.weekday() >= 5,
        "business_day_adjustment": "NOT_CALCULATED_CHECK_USPTO_OR_TTAB",
    }


def calculate_office_action_deadline(
    *,
    issue_date: date,
    as_of: date,
    madrid_66a: bool,
    final_action: bool = False,
    notice_stated_deadline: date | None = None,
) -> dict[str, Any]:
    """Calculate a standard pre-registration Office Action response schedule.

    The notice itself remains authoritative. Some Office Actions use a different
    response period, so an operator-provided notice deadline overrides the standard
    nominal deadline without changing the underlying rule metadata.
    """
    if as_of < issue_date:
        raise ValueError("as_of cannot be earlier than office action issue_date")
    if notice_stated_deadline is not None and notice_stated_deadline < issue_date:
        raise ValueError("notice_stated_deadline cannot be earlier than issue_date")

    if madrid_66a:
        standard_initial = add_months(issue_date, 6)
        extension_available = False
        standard_extended = None
        regime = "SECTION_66A_SIX_MONTH"
        basis_refs = ["USPTO_RESPONSE_TIME_PERIOD", "TMEP_711"]
    elif issue_date >= SHORTENED_OA_CUTOFF:
        standard_initial = add_months(issue_date, 3)
        extension_available = True
        standard_extended = add_months(issue_date, 6)
        regime = "SECTION_1_44_THREE_PLUS_OPTIONAL_THREE"
        basis_refs = ["USPTO_RESPONSE_FORMS", "TMEP_711"]
    else:
        standard_initial = add_months(issue_date, 6)
        extension_available = False
        standard_extended = None
        regime = "PRE_2022_12_03_LEGACY_SIX_MONTH"
        basis_refs = ["USPTO_RESPONSE_TIME_PERIOD", "TMEP_711"]

    operational_deadline = notice_stated_deadline or standard_initial
    result: dict[str, Any] = {
        "rule_version": APPLICATION_DEADLINE_RULE_VERSION,
        "rule_verified_on": APPLICATION_DEADLINE_RULE_VERIFIED_ON,
        "kind": "FINAL_OFFICE_ACTION" if final_action else "NONFINAL_OFFICE_ACTION",
        "regime": regime,
        "issue_date": issue_date,
        "madrid_66a": madrid_66a,
        "standard_initial_deadline": standard_initial,
        "extension_available": extension_available,
        "standard_deadline_if_extension_granted": standard_extended,
        "notice_stated_deadline": notice_stated_deadline,
        "operational_deadline_source": (
            "NOTICE_STATED_DEADLINE" if notice_stated_deadline else "STANDARD_RULE_NOMINAL"
        ),
        "operational_deadline": operational_deadline,
        "operational_deadline_state": _state(as_of, operational_deadline),
        "operational_deadline_falls_on_weekend": operational_deadline.weekday() >= 5,
        "business_day_adjustment": "NOT_CALCULATED_CHECK_OFFICIAL_NOTICE_AND_USPTO",
        "extension_request_rule": (
            "ONE_PAID_THREE_MONTH_EXTENSION_BEFORE_INITIAL_DEADLINE"
            if extension_available
            else "NO_STANDARD_EXTENSION_MODELED"
        ),
        "evidence_refs": basis_refs,
        "semantics": "DEADLINE_CALCULATION_NOT_APPLICATION_LEGAL_STATUS",
        "warnings": [
            "The Office Action itself controls if it specifies a different response period.",
            "Eastern Time and USPTO business-day rules must be checked before filing.",
        ],
    }
    if final_action:
        result["final_action_reminder"] = (
            "A final Office Action may also create a TTAB appeal deadline; this calculator does not infer appeal strategy or eligibility."
        )
    return result


def calculate_itu_noa_schedule(
    *,
    notice_of_allowance_date: date,
    as_of: date,
    extensions_granted: int | None = None,
    statement_of_use_filed: bool = False,
) -> dict[str, Any]:
    """Calculate the statutory Section 1(b) NOA/SOU extension ladder.

    Without an explicit count of granted extensions, the engine shows the full
    potential statutory ladder but does not claim which six-month period is current.
    """
    if as_of < notice_of_allowance_date:
        raise ValueError("as_of cannot be earlier than notice_of_allowance_date")
    if extensions_granted is not None and not 0 <= extensions_granted <= 5:
        raise ValueError("extensions_granted must be between 0 and 5")

    periods: list[dict[str, Any]] = []
    for granted_before_period in range(0, 6):
        due = add_months(notice_of_allowance_date, 6 * (granted_before_period + 1))
        if granted_before_period < 5:
            required_action = f"FILE_SOU_OR_EXTENSION_{granted_before_period + 1}"
        else:
            required_action = "FILE_SOU_FINAL_STATUTORY_DEADLINE"
        periods.append(
            {
                "period_index": granted_before_period,
                "extensions_already_granted": granted_before_period,
                "nominal_deadline": due,
                "required_action": required_action,
                "state_as_of": _state(as_of, due),
                "falls_on_weekend": due.weekday() >= 5,
                "business_day_adjustment": "NOT_CALCULATED_CHECK_USPTO",
                "later_extension_good_cause_note": (
                    "GOOD_CAUSE_REQUIRED_FOR_LATER_EXTENSION_REQUEST"
                    if granted_before_period >= 1 and granted_before_period < 5
                    else None
                ),
            }
        )

    current: dict[str, Any]
    if statement_of_use_filed:
        current = {
            "assessment": "SOU_REPORTED_FILED",
            "note": "Filing is reported, but acceptance/timeliness is not inferred by this deadline calculator.",
        }
    elif extensions_granted is None:
        current = {
            "assessment": "CURRENT_PERIOD_UNKNOWN",
            "reason": "extensions_granted_not_supplied",
        }
    else:
        current_period = periods[extensions_granted]
        current = {
            "assessment": "CURRENT_PERIOD_FROM_EXPLICIT_EXTENSION_COUNT",
            **current_period,
        }

    return {
        "rule_version": APPLICATION_DEADLINE_RULE_VERSION,
        "rule_verified_on": APPLICATION_DEADLINE_RULE_VERIFIED_ON,
        "kind": "INTENT_TO_USE_NOTICE_OF_ALLOWANCE",
        "notice_of_allowance_date": notice_of_allowance_date,
        "maximum_extension_requests": 5,
        "maximum_sou_deadline": add_months(notice_of_allowance_date, 36),
        "potential_periods": periods,
        "current_deadline_assessment": current,
        "evidence_refs": ["USPTO_ITU_FORMS", "USPTO_SOU_MINIMUM"],
        "semantics": "POTENTIAL_STATUTORY_SCHEDULE_NOT_APPLICATION_STATUS",
        "warnings": [
            "A granted extension is a fact that must come from USPTO records or an operator-supplied record; it is not inferred from elapsed time.",
            "Business-day adjustments are not calculated here.",
        ],
    }


def calculate_publication_opposition_schedule(
    *,
    publication_date: date,
    as_of: date,
    extension_days_granted: int | None = None,
) -> dict[str, Any]:
    """Calculate the publication opposition period and possible TTAB extensions.

    `extension_days_granted` is total extension time beyond the original 30-day
    opposition period. Only 0, 30, 90, or 150 are accepted because those correspond
    to the currently modeled TTAB extension paths. A grant is never inferred.
    """
    if as_of < publication_date:
        raise ValueError("as_of cannot be earlier than publication_date")
    allowed_grants = {0, 30, 90, 150}
    if extension_days_granted is not None and extension_days_granted not in allowed_grants:
        raise ValueError("extension_days_granted must be one of 0, 30, 90, 150")

    original_due = publication_date + timedelta(days=30)
    options = [
        {
            "path": "NO_EXTENSION",
            "total_extension_days": 0,
            "nominal_opposition_deadline": original_due,
            "grant_requirement": "NONE",
        },
        {
            "path": "INITIAL_30_DAY_EXTENSION",
            "total_extension_days": 30,
            "nominal_opposition_deadline": publication_date + timedelta(days=60),
            "grant_requirement": "GRANTED_UPON_TIMELY_REQUEST",
        },
        {
            "path": "TOTAL_90_DAY_EXTENSION",
            "total_extension_days": 90,
            "nominal_opposition_deadline": publication_date + timedelta(days=120),
            "grant_requirement": "GOOD_CAUSE_AND_FEE; MAY_BE INITIAL_90 OR 30_PLUS_60",
        },
        {
            "path": "FINAL_60_AFTER_TOTAL_90",
            "total_extension_days": 150,
            "nominal_opposition_deadline": publication_date + timedelta(days=180),
            "grant_requirement": "APPLICANT_CONSENT_OR_EXTRAORDINARY_CIRCUMSTANCES",
        },
    ]

    if extension_days_granted is None:
        current = {
            "assessment": "CURRENT_EXTENDED_DEADLINE_UNKNOWN",
            "original_opposition_deadline": original_due,
            "reason": "TTAB_extension_grant_facts_not_supplied",
        }
    else:
        selected = next(
            item for item in options if item["total_extension_days"] == extension_days_granted
        )
        current = {
            "assessment": "DEADLINE_FROM_EXPLICIT_EXTENSION_GRANT_FACT",
            **selected,
            **_deadline_metadata(selected["nominal_opposition_deadline"], as_of),
        }

    return {
        "rule_version": APPLICATION_DEADLINE_RULE_VERSION,
        "rule_verified_on": APPLICATION_DEADLINE_RULE_VERIFIED_ON,
        "kind": "PUBLICATION_OPPOSITION_PERIOD",
        "publication_date": publication_date,
        "original_opposition_deadline": original_due,
        "original_deadline_state": _state(as_of, original_due),
        "potential_extension_paths": options,
        "current_deadline_assessment": current,
        "evidence_refs": ["USPTO_TTAB_EXTENSIONS", "USPTO_TRADEMARK_PROCESS"],
        "semantics": "THIRD_PARTY_OPPOSITION_WINDOW_NOT_APPLICATION_LEGAL_STATUS",
        "warnings": [
            "TTAB extension requests must be timely and grants are party-specific facts; this calculator never assumes an extension was granted.",
            "Business-day adjustments are not calculated here.",
        ],
    }


def calculate_application_deadlines(
    *,
    as_of: date,
    madrid_66a: bool,
    publication_date: date | None = None,
    office_action_issue_date: date | None = None,
    office_action_final: bool = False,
    office_action_notice_deadline: date | None = None,
    notice_of_allowance_date: date | None = None,
    itu_extensions_granted: int | None = None,
    statement_of_use_filed: bool = False,
    opposition_extension_days_granted: int | None = None,
) -> dict[str, Any]:
    components: dict[str, Any] = {}
    missing_evidence: list[str] = []

    if publication_date is not None:
        components["publication_opposition"] = calculate_publication_opposition_schedule(
            publication_date=publication_date,
            as_of=as_of,
            extension_days_granted=opposition_extension_days_granted,
        )
    else:
        missing_evidence.append("publication_date")

    if office_action_issue_date is not None:
        components["office_action"] = calculate_office_action_deadline(
            issue_date=office_action_issue_date,
            as_of=as_of,
            madrid_66a=madrid_66a,
            final_action=office_action_final,
            notice_stated_deadline=office_action_notice_deadline,
        )
    else:
        missing_evidence.append("office_action_issue_date")

    if notice_of_allowance_date is not None:
        if madrid_66a:
            components["notice_of_allowance"] = {
                "status": "NOT_APPLICABLE_TO_SECTION_66A",
                "reason": "Notice of Allowance/SOU schedule is a Section 1(b) workflow",
            }
        else:
            components["notice_of_allowance"] = calculate_itu_noa_schedule(
                notice_of_allowance_date=notice_of_allowance_date,
                as_of=as_of,
                extensions_granted=itu_extensions_granted,
                statement_of_use_filed=statement_of_use_filed,
            )
    else:
        missing_evidence.append("notice_of_allowance_date")

    return {
        "rule_version": APPLICATION_DEADLINE_RULE_VERSION,
        "rule_verified_on": APPLICATION_DEADLINE_RULE_VERIFIED_ON,
        "as_of": as_of,
        "madrid_66a": madrid_66a,
        "components": components,
        "missing_evidence": missing_evidence,
        "evidence_refs": list(EVIDENCE_REFS),
        "semantics": "DEADLINE_METADATA_ONLY_NO_APPLICATION_LEGAL_STATUS",
    }
