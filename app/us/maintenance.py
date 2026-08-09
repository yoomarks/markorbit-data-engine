from __future__ import annotations

import calendar
from datetime import date
from typing import Any


MAINTENANCE_RULE_VERSION = "US_MAINTENANCE_2026_08_09_V1"
MAINTENANCE_RULE_VERIFIED_ON = date(2026, 8, 9)
MODERN_TERM_CUTOFF = date(1989, 11, 16)

EVIDENCE_REFS: tuple[dict[str, str], ...] = (
    {
        "id": "USPTO_KEEPING_REGISTRATION_ALIVE",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/maintain/keeping-your-registration-alive",
        "supports": "Section 8/9 and 71 filing windows, grace periods, Section 15 overview",
    },
    {
        "id": "USPTO_POST_REG_NON_MADRID",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/trademark-timelines/post-registration-timeline-all-registrations-except-madrid-protocol",
        "supports": "Non-Madrid Section 8, Section 9, Section 15 timing",
    },
    {
        "id": "USPTO_POST_REG_MADRID",
        "authority": "USPTO",
        "url": "https://www.uspto.gov/trademarks/trademark-timelines/post-registration-timeline-madrid-protocol-based-registrations",
        "supports": "Section 71 timing, Section 15, and WIPO international-registration renewal reminder",
    },
    {
        "id": "TMEP_1602_TERM",
        "authority": "USPTO_TMEP",
        "url": "https://tmep.uspto.gov/RDMS/TMEP/print?href=TMEP-1600d1e1.html&version=current",
        "supports": "Ten-year terms after 1989-11-16, legacy 20-year terms, Section 9 exclusion for Madrid",
    },
)


SECTION_15_EXTERNAL_FACTS = (
    "principal_register",
    "continuous_use_in_commerce_for_at_least_five_years",
    "no_adverse_legal_decision_involving_registered_rights",
    "no_pending_legal_proceeding_involving_registered_rights",
)


def add_years(value: date, years: int) -> date:
    """Calendar-year arithmetic with Feb. 29 clamped to Feb. 28."""
    target_year = value.year + years
    day = min(value.day, calendar.monthrange(target_year, value.month)[1])
    return date(target_year, value.month, day)


def add_months(value: date, months: int) -> date:
    """Calendar-month arithmetic with month-end clamping."""
    total = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _window_state(as_of: date, opens: date, regular_due: date, grace_due: date) -> str:
    if as_of < opens:
        return "FUTURE"
    if as_of <= regular_due:
        return "OPEN_REGULAR"
    if as_of <= grace_due:
        return "OPEN_GRACE"
    return "PAST_DEADLINE"


def _filing_window(
    *,
    code: str,
    label: str,
    opens: date,
    regular_due: date,
    as_of: date,
    required: bool,
    authority: str = "USPTO",
    evidence_refs: tuple[str, ...],
    basis: str,
) -> dict[str, Any]:
    grace_due = add_months(regular_due, 6)
    return {
        "code": code,
        "label": label,
        "authority": authority,
        "required": required,
        "opens_on": opens,
        "nominal_regular_deadline": regular_due,
        "nominal_grace_deadline": grace_due,
        "state_as_of": _window_state(as_of, opens, regular_due, grace_due),
        "basis": basis,
        "business_day_adjustment": "NOT_CALCULATED_CHECK_USPTO",
        "evidence_refs": list(evidence_refs),
        "rule_version": MAINTENANCE_RULE_VERSION,
        "rule_verified_on": MAINTENANCE_RULE_VERIFIED_ON,
    }


def _wipo_due(
    *,
    international_registration_date: date,
    term_year: int,
    as_of: date,
) -> dict[str, Any]:
    due = add_years(international_registration_date, term_year)
    if as_of < due:
        state = "FUTURE"
    elif as_of == due:
        state = "DUE_NOMINAL_DATE"
    else:
        state = "PAST_NOMINAL_DATE"
    return {
        "code": "WIPO_INTERNATIONAL_RENEWAL",
        "label": f"WIPO international registration renewal – year {term_year}",
        "authority": "WIPO_IB",
        "required": True,
        "nominal_due_date": due,
        "state_as_of": state,
        "basis": "INTERNATIONAL_REGISTRATION_DATE",
        "grace_period": "NOT_MODELED_CHECK_WIPO",
        "evidence_refs": ["USPTO_POST_REG_MADRID"],
        "rule_version": MAINTENANCE_RULE_VERSION,
        "rule_verified_on": MAINTENANCE_RULE_VERIFIED_ON,
    }


def _decennial_terms(registration_date: date, as_of: date) -> list[int]:
    age = max(0, as_of.year - registration_date.year)
    max_term = max(30, ((age + 29) // 10) * 10)
    return list(range(10, max_term + 1, 10))


def calculate_maintenance_schedule(
    *,
    registration_date: date,
    as_of: date,
    madrid_66a: bool = False,
    international_registration_date: date | None = None,
    current_term_expiration_date: date | None = None,
) -> dict[str, Any]:
    """Calculate filing windows without inferring whether a registration is legally live.

    The output is deadline metadata only. It never concludes that a registration is
    active, cancelled, expired, incontestable, or otherwise legally valid.
    """
    if as_of < registration_date:
        raise ValueError("as_of cannot be earlier than registration_date")

    result: dict[str, Any] = {
        "rule_version": MAINTENANCE_RULE_VERSION,
        "rule_verified_on": MAINTENANCE_RULE_VERIFIED_ON,
        "authority": "USPTO",
        "semantics": "DEADLINE_CALCULATION_NOT_CASE_LEGAL_STATUS",
        "registration_date": registration_date,
        "as_of": as_of,
        "madrid_66a": madrid_66a,
        "legacy_term": registration_date < MODERN_TERM_CUTOFF,
        "obligations": [],
        "optional_filings": [],
        "external_reminders": [],
        "evidence_refs": list(EVIDENCE_REFS),
        "warnings": [
            "Nominal deadlines are not shifted for Saturday, Sunday, or federal holidays; verify the USPTO business-day rule before filing."
        ],
    }

    if registration_date < MODERN_TERM_CUTOFF:
        result["mode"] = "LEGACY_TERM_REQUIRES_RENEWAL_HISTORY"
        result["warnings"].append(
            "Registration predates 1989-11-16; do not assume a modern ten-year term without renewal history or an explicit current-term expiration date."
        )
        if current_term_expiration_date is not None and not madrid_66a:
            result["obligations"].append(
                _filing_window(
                    code="SECTIONS_8_9_LEGACY_CURRENT_TERM",
                    label="Sections 8 and 9 – operator-supplied current term",
                    opens=add_years(current_term_expiration_date, -1),
                    regular_due=current_term_expiration_date,
                    as_of=as_of,
                    required=True,
                    evidence_refs=("TMEP_1602_TERM", "USPTO_POST_REG_NON_MADRID"),
                    basis="OPERATOR_SUPPLIED_CURRENT_TERM_EXPIRATION",
                )
            )
        result["optional_filings"].append(
            {
                "code": "SECTION_15",
                "label": "Section 15 Declaration of Incontestability",
                "eligibility": "REQUIRES_LEGACY_REGISTRATION_AND_USE_FACTS",
                "required": False,
                "external_facts_required": list(SECTION_15_EXTERNAL_FACTS),
                "evidence_refs": ["USPTO_POST_REG_NON_MADRID"],
                "rule_version": MAINTENANCE_RULE_VERSION,
                "rule_verified_on": MAINTENANCE_RULE_VERIFIED_ON,
            }
        )
        return result

    result["mode"] = "MODERN_MADRID" if madrid_66a else "MODERN_NON_MADRID"

    first_code = "SECTION_71_FIRST" if madrid_66a else "SECTION_8_FIRST"
    first_label = (
        "Section 71 Declaration – first filing"
        if madrid_66a
        else "Section 8 Declaration – first filing"
    )
    result["obligations"].append(
        _filing_window(
            code=first_code,
            label=first_label,
            opens=add_years(registration_date, 5),
            regular_due=add_years(registration_date, 6),
            as_of=as_of,
            required=True,
            evidence_refs=(
                "USPTO_POST_REG_MADRID" if madrid_66a else "USPTO_POST_REG_NON_MADRID",
                "USPTO_KEEPING_REGISTRATION_ALIVE",
            ),
            basis="US_REGISTRATION_DATE",
        )
    )

    for term_year in _decennial_terms(registration_date, as_of):
        if madrid_66a:
            code = "SECTION_71_DECENNIAL"
            label = f"Section 71 Declaration – year {term_year}"
            evidence = ("USPTO_POST_REG_MADRID", "USPTO_KEEPING_REGISTRATION_ALIVE")
        else:
            code = "SECTIONS_8_9"
            label = f"Combined Sections 8 and 9 – year {term_year}"
            evidence = ("USPTO_POST_REG_NON_MADRID", "USPTO_KEEPING_REGISTRATION_ALIVE")
        result["obligations"].append(
            _filing_window(
                code=code,
                label=label,
                opens=add_years(registration_date, term_year - 1),
                regular_due=add_years(registration_date, term_year),
                as_of=as_of,
                required=True,
                evidence_refs=evidence,
                basis="US_REGISTRATION_DATE",
            )
        )

    result["optional_filings"].append(
        {
            "code": "SECTION_15",
            "label": "Section 15 Declaration of Incontestability",
            "required": False,
            "earliest_possible_date": add_years(registration_date, 5),
            "eligibility": "REQUIRES_EXTERNAL_FACTS",
            "external_facts_required": list(SECTION_15_EXTERNAL_FACTS),
            "note": "Registration age alone is insufficient to determine Section 15 eligibility.",
            "evidence_refs": [
                "USPTO_POST_REG_NON_MADRID" if not madrid_66a else "USPTO_POST_REG_MADRID"
            ],
            "rule_version": MAINTENANCE_RULE_VERSION,
            "rule_verified_on": MAINTENANCE_RULE_VERIFIED_ON,
        }
    )

    if madrid_66a:
        if international_registration_date is None:
            result["warnings"].append(
                "Madrid registration has no international_registration_date; WIPO ten-year renewal reminders were not calculated."
            )
        else:
            age = max(0, as_of.year - international_registration_date.year)
            max_term = max(20, ((age + 29) // 10) * 10)
            for term_year in range(10, max_term + 1, 10):
                result["external_reminders"].append(
                    _wipo_due(
                        international_registration_date=international_registration_date,
                        term_year=term_year,
                        as_of=as_of,
                    )
                )

    return result
