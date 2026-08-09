from datetime import date

import pytest

from app.us.application_deadlines import (
    APPLICATION_DEADLINE_RULE_VERSION,
    calculate_application_deadlines,
    calculate_itu_noa_schedule,
    calculate_office_action_deadline,
    calculate_publication_opposition_schedule,
)


def test_section_1_44_office_action_is_three_months_plus_optional_three() -> None:
    report = calculate_office_action_deadline(
        issue_date=date(2026, 1, 31),
        as_of=date(2026, 2, 1),
        madrid_66a=False,
    )
    assert report["rule_version"] == APPLICATION_DEADLINE_RULE_VERSION
    assert report["regime"] == "SECTION_1_44_THREE_PLUS_OPTIONAL_THREE"
    assert report["standard_initial_deadline"] == date(2026, 4, 30)
    assert report["standard_deadline_if_extension_granted"] == date(2026, 7, 31)
    assert report["extension_available"] is True


def test_madrid_office_action_is_six_months_without_standard_extension() -> None:
    report = calculate_office_action_deadline(
        issue_date=date(2026, 2, 28),
        as_of=date(2026, 3, 1),
        madrid_66a=True,
    )
    assert report["regime"] == "SECTION_66A_SIX_MONTH"
    assert report["standard_initial_deadline"] == date(2026, 8, 28)
    assert report["extension_available"] is False
    assert report["standard_deadline_if_extension_granted"] is None


def test_pre_cutoff_office_action_fails_safe_to_legacy_six_month_period() -> None:
    report = calculate_office_action_deadline(
        issue_date=date(2022, 12, 2),
        as_of=date(2022, 12, 3),
        madrid_66a=False,
    )
    assert report["regime"] == "PRE_2022_12_03_LEGACY_SIX_MONTH"
    assert report["standard_initial_deadline"] == date(2023, 6, 2)
    assert report["extension_available"] is False


def test_office_action_notice_stated_deadline_controls_operational_date() -> None:
    report = calculate_office_action_deadline(
        issue_date=date(2026, 1, 15),
        as_of=date(2026, 2, 1),
        madrid_66a=False,
        notice_stated_deadline=date(2026, 5, 20),
        final_action=True,
    )
    assert report["standard_initial_deadline"] == date(2026, 4, 15)
    assert report["operational_deadline"] == date(2026, 5, 20)
    assert report["operational_deadline_source"] == "NOTICE_STATED_DEADLINE"
    assert "TTAB appeal" in report["final_action_reminder"]


def test_noa_schedule_has_five_extensions_and_final_deadline_at_36_months() -> None:
    report = calculate_itu_noa_schedule(
        notice_of_allowance_date=date(2024, 1, 31),
        as_of=date(2026, 8, 1),
        extensions_granted=5,
    )
    assert report["maximum_extension_requests"] == 5
    assert report["maximum_sou_deadline"] == date(2027, 1, 31)
    assert len(report["potential_periods"]) == 6
    final = report["current_deadline_assessment"]
    assert final["extensions_already_granted"] == 5
    assert final["nominal_deadline"] == date(2027, 1, 31)
    assert final["required_action"] == "FILE_SOU_FINAL_STATUTORY_DEADLINE"


def test_noa_schedule_never_infers_granted_extensions_from_elapsed_time() -> None:
    report = calculate_itu_noa_schedule(
        notice_of_allowance_date=date(2024, 1, 1),
        as_of=date(2026, 1, 2),
    )
    current = report["current_deadline_assessment"]
    assert current["assessment"] == "CURRENT_PERIOD_UNKNOWN"
    assert current["reason"] == "extensions_granted_not_supplied"


def test_reported_sou_does_not_imply_acceptance_or_timeliness() -> None:
    report = calculate_itu_noa_schedule(
        notice_of_allowance_date=date(2025, 1, 1),
        as_of=date(2026, 1, 1),
        statement_of_use_filed=True,
    )
    current = report["current_deadline_assessment"]
    assert current["assessment"] == "SOU_REPORTED_FILED"
    assert "acceptance/timeliness is not inferred" in current["note"]


def test_publication_original_and_extension_ladder() -> None:
    publication = date(2026, 3, 1)
    base = calculate_publication_opposition_schedule(
        publication_date=publication,
        as_of=date(2026, 3, 2),
        extension_days_granted=0,
    )
    assert base["original_opposition_deadline"] == date(2026, 3, 31)
    assert base["current_deadline_assessment"]["nominal_opposition_deadline"] == date(
        2026, 3, 31
    )

    plus_30 = calculate_publication_opposition_schedule(
        publication_date=publication,
        as_of=date(2026, 3, 2),
        extension_days_granted=30,
    )
    assert plus_30["current_deadline_assessment"]["nominal_opposition_deadline"] == date(
        2026, 4, 30
    )

    total_90 = calculate_publication_opposition_schedule(
        publication_date=publication,
        as_of=date(2026, 3, 2),
        extension_days_granted=90,
    )
    assert total_90["current_deadline_assessment"]["nominal_opposition_deadline"] == date(
        2026, 6, 29
    )

    total_150 = calculate_publication_opposition_schedule(
        publication_date=publication,
        as_of=date(2026, 3, 2),
        extension_days_granted=150,
    )
    assert total_150["current_deadline_assessment"]["nominal_opposition_deadline"] == date(
        2026, 8, 28
    )


def test_publication_extension_grant_is_never_assumed() -> None:
    report = calculate_publication_opposition_schedule(
        publication_date=date(2026, 3, 1),
        as_of=date(2026, 4, 1),
    )
    assert report["current_deadline_assessment"]["assessment"] == (
        "CURRENT_EXTENDED_DEADLINE_UNKNOWN"
    )


def test_invalid_publication_extension_total_rejected() -> None:
    with pytest.raises(ValueError, match="0, 30, 90, 150"):
        calculate_publication_opposition_schedule(
            publication_date=date(2026, 3, 1),
            as_of=date(2026, 3, 2),
            extension_days_granted=60,
        )


def test_combined_madrid_schedule_rejects_noa_workflow_as_not_applicable() -> None:
    report = calculate_application_deadlines(
        as_of=date(2026, 8, 9),
        madrid_66a=True,
        publication_date=date(2026, 7, 1),
        notice_of_allowance_date=date(2026, 7, 15),
    )
    assert report["components"]["notice_of_allowance"]["status"] == (
        "NOT_APPLICABLE_TO_SECTION_66A"
    )
    assert report["semantics"] == "DEADLINE_METADATA_ONLY_NO_APPLICATION_LEGAL_STATUS"


def test_combined_schedule_lists_missing_explicit_evidence() -> None:
    report = calculate_application_deadlines(
        as_of=date(2026, 8, 9),
        madrid_66a=False,
        publication_date=date(2026, 7, 1),
    )
    assert "office_action_issue_date" in report["missing_evidence"]
    assert "notice_of_allowance_date" in report["missing_evidence"]
    assert "publication_date" not in report["missing_evidence"]
