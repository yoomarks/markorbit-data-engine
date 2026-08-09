from __future__ import annotations

from datetime import date
import json

from app.us.application_deadlines import (
    calculate_application_deadlines,
    calculate_itu_noa_schedule,
    calculate_office_action_deadline,
    calculate_publication_opposition_schedule,
)


def main() -> None:
    standard_oa = calculate_office_action_deadline(
        issue_date=date(2026, 1, 31),
        as_of=date(2026, 2, 1),
        madrid_66a=False,
    )
    if standard_oa["standard_initial_deadline"] != date(2026, 4, 30):
        raise RuntimeError(f"Standard OA three-month deadline failed: {standard_oa}")
    if standard_oa["standard_deadline_if_extension_granted"] != date(2026, 7, 31):
        raise RuntimeError(f"Standard OA extension deadline failed: {standard_oa}")

    madrid_oa = calculate_office_action_deadline(
        issue_date=date(2026, 2, 28),
        as_of=date(2026, 3, 1),
        madrid_66a=True,
    )
    if madrid_oa["standard_initial_deadline"] != date(2026, 8, 28):
        raise RuntimeError(f"Madrid OA six-month deadline failed: {madrid_oa}")
    if madrid_oa["extension_available"] is not False:
        raise RuntimeError(f"Madrid OA incorrectly offered extension: {madrid_oa}")

    noa = calculate_itu_noa_schedule(
        notice_of_allowance_date=date(2024, 1, 31),
        as_of=date(2026, 8, 9),
        extensions_granted=5,
    )
    if noa["maximum_extension_requests"] != 5:
        raise RuntimeError(f"NOA extension cap failed: {noa}")
    if noa["maximum_sou_deadline"] != date(2027, 1, 31):
        raise RuntimeError(f"NOA 36-month maximum failed: {noa}")
    if noa["current_deadline_assessment"]["required_action"] != (
        "FILE_SOU_FINAL_STATUTORY_DEADLINE"
    ):
        raise RuntimeError(f"NOA final SOU action failed: {noa}")

    unknown_noa = calculate_itu_noa_schedule(
        notice_of_allowance_date=date(2024, 1, 31),
        as_of=date(2026, 8, 9),
    )
    if unknown_noa["current_deadline_assessment"]["assessment"] != (
        "CURRENT_PERIOD_UNKNOWN"
    ):
        raise RuntimeError(f"NOA extension facts were inferred: {unknown_noa}")

    opposition = calculate_publication_opposition_schedule(
        publication_date=date(2026, 3, 1),
        as_of=date(2026, 3, 2),
        extension_days_granted=150,
    )
    if opposition["original_opposition_deadline"] != date(2026, 3, 31):
        raise RuntimeError(f"Original opposition period failed: {opposition}")
    if opposition["current_deadline_assessment"]["nominal_opposition_deadline"] != date(
        2026, 8, 28
    ):
        raise RuntimeError(f"TTAB extension ladder failed: {opposition}")

    combined = calculate_application_deadlines(
        as_of=date(2026, 8, 9),
        madrid_66a=False,
        publication_date=date(2026, 6, 1),
        office_action_issue_date=date(2026, 2, 10),
        notice_of_allowance_date=date(2026, 7, 1),
        itu_extensions_granted=0,
        opposition_extension_days_granted=30,
    )
    if combined["semantics"] != "DEADLINE_METADATA_ONLY_NO_APPLICATION_LEGAL_STATUS":
        raise RuntimeError(f"Deadline semantics boundary changed: {combined}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "contract": "US_APPLICATION_DEADLINES_FIXTURE",
                "section_1_44_oa_three_plus_three": "PASS",
                "madrid_66a_oa_six_month_no_extension": "PASS",
                "itu_noa_five_extensions_and_36_month_cap": "PASS",
                "itu_extension_count_not_inferred": "PASS",
                "publication_30_day_opposition": "PASS",
                "ttab_extension_ladder": "PASS",
                "application_legal_status_inference": "NONE",
                "automatic_unknown_event_code_inference": "NONE",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
