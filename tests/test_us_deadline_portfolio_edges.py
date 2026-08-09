from datetime import date

from app.us.deadline_portfolio import build_case_deadline_candidates


def test_recently_missed_maintenance_grace_uses_grace_deadline_not_regular_deadline() -> None:
    case = {
        "serial_number": "88994001",
        "registration_number": "7001001",
        "registration_date": date(2020, 2, 1),
        "publication_date": None,
        "madrid_66a": 0,
        "madrid_66a_current": 0,
        "international_registration_date": None,
    }
    candidates = build_case_deadline_candidates(
        case=case,
        events=[],
        role_state={"status": "NOT_READY", "roles": {}},
        as_of=date(2026, 8, 5),
        horizon_days=90,
        recent_past_days=30,
    )
    row = next(item for item in candidates if item["code"] == "SECTION_8_FIRST")
    assert row["due_date"] == date(2026, 8, 1)
    assert row["urgency"] == "RECENT_PAST_NOMINAL_DEADLINE"
    assert row["details"]["regular_deadline"] == date(2026, 2, 1)
    assert row["details"]["grace_deadline"] == date(2026, 8, 1)


def test_opposition_extension_event_before_current_publication_is_not_reused() -> None:
    case = {
        "serial_number": "88994002",
        "registration_number": "",
        "registration_date": None,
        "publication_date": date(2026, 8, 1),
        "madrid_66a": 0,
        "madrid_66a_current": 0,
        "international_registration_date": None,
    }
    role_state = {
        "status": "PASS",
        "ruleset": {"ruleset_version": "ROLES_V1"},
        "roles": {
            "OP90": {
                "event_code": "OP90",
                "role": "OPPOSITION_EXTENSION_90_GRANTED",
                "rule_id": "OP90",
                "source_refs": ["review"],
            }
        },
    }
    events = [
        {
            "event_code": "OP90",
            "event_date": date(2026, 7, 15),
            "event_sequence": 1,
            "event_type": "fixture",
            "description_text": "old publication cycle extension",
        }
    ]
    candidates = build_case_deadline_candidates(
        case=case,
        events=events,
        role_state=role_state,
        as_of=date(2026, 8, 9),
        horizon_days=180,
        recent_past_days=30,
    )
    row = next(item for item in candidates if item["code"] == "OPPOSITION_PERIOD")
    assert row["due_date"] == date(2026, 8, 31)
    assert row["details"]["extension_days_granted"] is None
    assert row["details"]["extension_facts_known"] is False
