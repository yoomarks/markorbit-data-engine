from datetime import date

import pytest

from app.us.maintenance import (
    MAINTENANCE_RULE_VERSION,
    add_months,
    add_years,
    calculate_maintenance_schedule,
)


def _obligation(report: dict, code: str, *, label_contains: str | None = None) -> dict:
    rows = [row for row in report["obligations"] if row["code"] == code]
    if label_contains is not None:
        rows = [row for row in rows if label_contains in row["label"]]
    assert rows, (code, report["obligations"])
    return rows[0]


def test_calendar_arithmetic_clamps_leap_and_month_end() -> None:
    assert add_years(date(2020, 2, 29), 5) == date(2025, 2, 28)
    assert add_years(date(2020, 2, 29), 6) == date(2026, 2, 28)
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)


def test_non_madrid_first_section_8_and_decennial_windows() -> None:
    report = calculate_maintenance_schedule(
        registration_date=date(2020, 7, 28),
        as_of=date(2026, 8, 9),
    )
    assert report["mode"] == "MODERN_NON_MADRID"
    assert report["rule_version"] == MAINTENANCE_RULE_VERSION
    first = _obligation(report, "SECTION_8_FIRST")
    assert first["opens_on"] == date(2025, 7, 28)
    assert first["nominal_regular_deadline"] == date(2026, 7, 28)
    assert first["nominal_grace_deadline"] == date(2027, 1, 28)
    assert first["state_as_of"] == "OPEN_GRACE"

    renewal = _obligation(report, "SECTIONS_8_9", label_contains="year 10")
    assert renewal["opens_on"] == date(2029, 7, 28)
    assert renewal["nominal_regular_deadline"] == date(2030, 7, 28)
    assert renewal["state_as_of"] == "FUTURE"
    assert "LEGAL_STATUS" in report["semantics"]


def test_madrid_uses_section_71_and_separate_wipo_reminders() -> None:
    report = calculate_maintenance_schedule(
        registration_date=date(2017, 6, 15),
        as_of=date(2026, 8, 9),
        madrid_66a=True,
        international_registration_date=date(2016, 3, 1),
    )
    assert report["mode"] == "MODERN_MADRID"
    first = _obligation(report, "SECTION_71_FIRST")
    assert first["state_as_of"] == "PAST_DEADLINE"
    decennial = _obligation(report, "SECTION_71_DECENNIAL", label_contains="year 10")
    assert decennial["opens_on"] == date(2026, 6, 15)
    assert decennial["nominal_regular_deadline"] == date(2027, 6, 15)
    assert decennial["state_as_of"] == "OPEN_REGULAR"
    assert not any(row["code"] == "SECTIONS_8_9" for row in report["obligations"])

    wipo = report["external_reminders"][0]
    assert wipo["authority"] == "WIPO_IB"
    assert wipo["nominal_due_date"] == date(2026, 3, 1)
    assert wipo["state_as_of"] == "PAST_NOMINAL_DATE"
    assert wipo["grace_period"] == "NOT_MODELED_CHECK_WIPO"


def test_section_15_never_becomes_automatic_eligibility_conclusion() -> None:
    report = calculate_maintenance_schedule(
        registration_date=date(2020, 1, 1),
        as_of=date(2026, 1, 2),
    )
    section_15 = report["optional_filings"][0]
    assert section_15["code"] == "SECTION_15"
    assert section_15["earliest_possible_date"] == date(2025, 1, 1)
    assert section_15["eligibility"] == "REQUIRES_EXTERNAL_FACTS"
    assert "principal_register" in section_15["external_facts_required"]
    assert "no_pending_legal_proceeding_involving_registered_rights" in section_15[
        "external_facts_required"
    ]


def test_pre_1989_registration_fails_safe_without_renewal_history() -> None:
    report = calculate_maintenance_schedule(
        registration_date=date(1988, 1, 1),
        as_of=date(2026, 8, 9),
    )
    assert report["mode"] == "LEGACY_TERM_REQUIRES_RENEWAL_HISTORY"
    assert report["obligations"] == []
    assert report["legacy_term"] is True


def test_legacy_explicit_current_term_can_calculate_one_window() -> None:
    report = calculate_maintenance_schedule(
        registration_date=date(1988, 1, 1),
        as_of=date(2027, 6, 1),
        current_term_expiration_date=date(2028, 1, 1),
    )
    window = _obligation(report, "SECTIONS_8_9_LEGACY_CURRENT_TERM")
    assert window["opens_on"] == date(2027, 1, 1)
    assert window["nominal_regular_deadline"] == date(2028, 1, 1)
    assert window["state_as_of"] == "OPEN_REGULAR"
    assert window["basis"] == "OPERATOR_SUPPLIED_CURRENT_TERM_EXPIRATION"


def test_as_of_before_registration_is_rejected() -> None:
    with pytest.raises(ValueError, match="as_of"):
        calculate_maintenance_schedule(
            registration_date=date(2026, 8, 10),
            as_of=date(2026, 8, 9),
        )
