from datetime import date

from app.us.deadline_portfolio import build_case_deadline_candidates


def _role_state() -> dict:
    return {
        "status": "PASS",
        "ruleset": {"ruleset_version": "ROLES_V1"},
        "roles": {
            "NRAP": {
                "event_code": "NRAP",
                "role": "OFFICE_ACTION_NONFINAL_ISSUED",
                "rule_id": "OA",
                "source_refs": ["r1"],
            },
            "NOA": {
                "event_code": "NOA",
                "role": "NOTICE_OF_ALLOWANCE_ISSUED",
                "rule_id": "NOA",
                "source_refs": ["r2"],
            },
            "EXT": {
                "event_code": "EXT",
                "role": "ITU_EXTENSION_GRANTED",
                "rule_id": "EXT",
                "source_refs": ["r3"],
            },
        },
    }


def _event(code: str, when: date, seq: int = 1) -> dict:
    return {
        "event_code": code,
        "event_date": when,
        "event_sequence": seq,
        "event_type": "fixture",
        "description_text": code,
    }


def test_case_candidates_include_maintenance_publication_and_reviewed_oa() -> None:
    case = {
        "serial_number": "88993001",
        "registration_number": "7000001",
        "registration_date": date(2020, 10, 15),
        "publication_date": date(2026, 8, 1),
        "madrid_66a": 0,
        "madrid_66a_current": 0,
        "international_registration_date": None,
    }
    candidates = build_case_deadline_candidates(
        case=case,
        events=[_event("NRAP", date(2026, 7, 20))],
        role_state=_role_state(),
        as_of=date(2026, 8, 9),
        horizon_days=120,
        recent_past_days=30,
    )
    codes = {row["code"] for row in candidates}
    assert "SECTION_8_FIRST" in codes
    assert "OPPOSITION_PERIOD" in codes
    assert "NONFINAL_OFFICE_ACTION_RESPONSE" in codes
    assert all(row["legal_status_inference"] is False for row in candidates)


def test_noa_candidate_requires_reviewed_extension_count_and_no_sou() -> None:
    case = {
        "serial_number": "88993002",
        "registration_number": "",
        "registration_date": None,
        "publication_date": None,
        "madrid_66a": 0,
        "madrid_66a_current": 0,
        "international_registration_date": None,
    }
    candidates = build_case_deadline_candidates(
        case=case,
        events=[
            _event("NOA", date(2026, 3, 1)),
            _event("EXT", date(2026, 8, 15)),
        ],
        role_state=_role_state(),
        as_of=date(2026, 8, 20),
        horizon_days=365,
        recent_past_days=30,
    )
    rows = [row for row in candidates if row["code"] == "ITU_SOU_OR_EXTENSION"]
    assert len(rows) == 1
    assert rows[0]["details"]["extensions_granted"] == 1
    assert rows[0]["source"] == "REVIEWED_EVENT_ROLE_PLUS_VERSIONED_NOA_RULE"


def test_without_reviewed_event_roles_only_direct_case_fact_candidates_remain() -> None:
    case = {
        "serial_number": "88993003",
        "registration_number": "",
        "registration_date": None,
        "publication_date": date(2026, 8, 1),
        "madrid_66a": 0,
        "madrid_66a_current": 0,
        "international_registration_date": None,
    }
    candidates = build_case_deadline_candidates(
        case=case,
        events=[_event("NRAP", date(2026, 7, 20))],
        role_state={
            "status": "NOT_READY",
            "reason": "active_event_role_ruleset_missing",
            "roles": {},
        },
        as_of=date(2026, 8, 9),
        horizon_days=90,
        recent_past_days=30,
    )
    assert [row["code"] for row in candidates] == ["OPPOSITION_PERIOD"]
