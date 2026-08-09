from datetime import date

from app.us.deadline_evidence import resolve_deadline_evidence


def _role_state() -> dict:
    roles = {
        "NRAP": {
            "event_code": "NRAP",
            "role": "OFFICE_ACTION_NONFINAL_ISSUED",
            "rule_id": "OA",
            "source_refs": ["r1"],
        },
        "ROA": {
            "event_code": "ROA",
            "role": "OFFICE_ACTION_RESPONSE_FILED",
            "rule_id": "ROA",
            "source_refs": ["r2"],
        },
        "NOA": {
            "event_code": "NOA",
            "role": "NOTICE_OF_ALLOWANCE_ISSUED",
            "rule_id": "NOA",
            "source_refs": ["r3"],
        },
        "EXT": {
            "event_code": "EXT",
            "role": "ITU_EXTENSION_GRANTED",
            "rule_id": "EXT",
            "source_refs": ["r4"],
        },
        "SOU": {
            "event_code": "SOU",
            "role": "STATEMENT_OF_USE_FILED",
            "rule_id": "SOU",
            "source_refs": ["r5"],
        },
        "OP90": {
            "event_code": "OP90",
            "role": "OPPOSITION_EXTENSION_90_GRANTED",
            "rule_id": "OP90",
            "source_refs": ["r6"],
        },
    }
    return {
        "status": "PASS",
        "ruleset": {"ruleset_version": "ROLES_V1"},
        "roles": roles,
    }


def _event(code: str, when: date, seq: int = 1) -> dict:
    return {
        "event_code": code,
        "event_date": when,
        "event_sequence": seq,
        "event_type": "fixture",
        "description_text": code,
    }


def test_unready_role_map_disables_all_automatic_inputs() -> None:
    report = resolve_deadline_evidence(
        events=[_event("NRAP", date(2026, 1, 1))],
        role_state={"status": "NOT_READY", "reason": "missing", "roles": {}},
    )
    assert report["status"] == "NOT_READY"
    assert all(value is None for value in report["automatic_inputs"].values())


def test_latest_unanswered_oa_becomes_automatic_deadline_input() -> None:
    report = resolve_deadline_evidence(
        events=[_event("NRAP", date(2026, 2, 10))],
        role_state=_role_state(),
    )
    assert report["automatic_inputs"]["office_action_issue_date"] == date(2026, 2, 10)
    assert report["automatic_inputs"]["office_action_final"] is False
    assert report["office_action"]["status"] == "PENDING_CANDIDATE_FROM_REVIEWED_EVENT_ROLE"


def test_mapped_response_suppresses_pending_oa_input() -> None:
    report = resolve_deadline_evidence(
        events=[
            _event("NRAP", date(2026, 2, 10)),
            _event("ROA", date(2026, 3, 1)),
        ],
        role_state=_role_state(),
    )
    assert report["office_action"]["status"] == "RESOLVED_BY_MAPPED_RESPONSE"
    assert report["automatic_inputs"]["office_action_issue_date"] is None


def test_noa_extension_count_and_sou_are_resolved_without_elapsed_time_guessing() -> None:
    report = resolve_deadline_evidence(
        events=[
            _event("NOA", date(2025, 1, 1)),
            _event("EXT", date(2025, 6, 20), 1),
            _event("EXT", date(2025, 12, 20), 2),
            _event("SOU", date(2026, 2, 1)),
        ],
        role_state=_role_state(),
    )
    assert report["automatic_inputs"]["notice_of_allowance_date"] == date(2025, 1, 1)
    assert report["automatic_inputs"]["itu_extensions_granted"] == 2
    assert report["automatic_inputs"]["statement_of_use_filed"] is True


def test_multiple_noa_dates_fail_closed() -> None:
    report = resolve_deadline_evidence(
        events=[
            _event("NOA", date(2025, 1, 1), 1),
            _event("NOA", date(2025, 2, 1), 2),
        ],
        role_state=_role_state(),
    )
    assert report["notice_of_allowance"]["status"] == "AMBIGUOUS_MULTIPLE_NOA_DATES"
    assert report["automatic_inputs"]["notice_of_allowance_date"] is None


def test_opposition_extension_uses_highest_reviewed_total_and_unknown_codes_remain_unknown() -> None:
    report = resolve_deadline_evidence(
        events=[
            _event("OP90", date(2026, 3, 20)),
            _event("UNKNOWN", date(2026, 3, 21)),
        ],
        role_state=_role_state(),
    )
    assert report["automatic_inputs"]["opposition_extension_days_granted"] == 90
    assert report["unmapped_event_codes"] == ["UNKNOWN"]
