from datetime import date

import pytest
from fastapi import HTTPException

import app.us.deadline_docket_api as api


def test_deadline_docket_router_is_get_only() -> None:
    assert api.router.routes
    for route in api.router.routes:
        assert route.methods == {"GET"}


def test_resolved_deadline_explicit_evidence_overrides_reviewed_event_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_query_case",
        lambda _serial: {
            "publication_date": date(2026, 6, 1),
            "intent_to_use_1b": 1,
            "intent_to_use_1b_filed": 1,
            "intent_to_use_1b_current": 1,
            "madrid_66a": 0,
            "madrid_66a_current": 0,
        },
    )
    monkeypatch.setattr(
        api,
        "resolve_case_deadline_evidence",
        lambda **_kwargs: {
            "status": "PASS",
            "automatic_inputs": {
                "office_action_issue_date": date(2026, 2, 10),
                "office_action_final": False,
                "notice_of_allowance_date": date(2026, 7, 1),
                "itu_extensions_granted": 1,
                "statement_of_use_filed": False,
                "opposition_extension_days_granted": 90,
            },
        },
    )
    result = api.us_application_deadlines_resolved(
        "97123456",
        as_of=date(2026, 8, 9),
        office_action_issue_date=date(2026, 3, 1),
        itu_extensions_granted=2,
        opposition_extension_days_granted=30,
    )
    assert result["input_provenance"]["publication_date"] == "OFFICIAL_USPTO_CASE_FACT"
    assert result["input_provenance"]["office_action_issue_date"] == "EXPLICIT_API_EVIDENCE"
    assert result["input_provenance"]["notice_of_allowance_date"] == "REVIEWED_EVENT_ROLE_EVIDENCE"
    assert result["input_provenance"]["itu_extensions_granted"] == "EXPLICIT_API_EVIDENCE"
    assert result["input_provenance"]["opposition_extension_days_granted"] == "EXPLICIT_API_EVIDENCE"
    assert result["schedule"]["components"]["office_action"]["issue_date"] == date(2026, 3, 1)
    assert result["schedule"]["components"]["notice_of_allowance"][
        "current_deadline_assessment"
    ]["extensions_already_granted"] == 2
    assert result["schedule"]["components"]["publication_opposition"][
        "current_deadline_assessment"
    ]["total_extension_days"] == 30


def test_resolved_deadline_uses_reviewed_event_evidence_when_explicit_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_query_case",
        lambda _serial: {
            "publication_date": None,
            "intent_to_use_1b": 1,
            "intent_to_use_1b_filed": 1,
            "intent_to_use_1b_current": 1,
            "madrid_66a": 0,
            "madrid_66a_current": 0,
        },
    )
    monkeypatch.setattr(
        api,
        "resolve_case_deadline_evidence",
        lambda **_kwargs: {
            "status": "PASS",
            "automatic_inputs": {
                "office_action_issue_date": date(2026, 2, 10),
                "office_action_final": True,
                "notice_of_allowance_date": date(2026, 7, 1),
                "itu_extensions_granted": 0,
                "statement_of_use_filed": False,
                "opposition_extension_days_granted": None,
            },
        },
    )
    result = api.us_application_deadlines_resolved(
        "97123456",
        as_of=date(2026, 8, 9),
    )
    assert result["input_provenance"]["office_action_issue_date"] == "REVIEWED_EVENT_ROLE_EVIDENCE"
    assert result["input_provenance"]["office_action_final"] == "REVIEWED_EVENT_ROLE_EVIDENCE"
    assert result["input_provenance"]["itu_extensions_granted"] == "REVIEWED_EVENT_ROLE_EVIDENCE"
    assert result["schedule"]["components"]["office_action"]["kind"] == "FINAL_OFFICE_ACTION"


def test_candidate_page_converts_invalid_cursor_to_422(monkeypatch) -> None:
    def _raise(**_kwargs):
        raise ValueError("after_serial must be empty or exactly 8 digits")

    monkeypatch.setattr(api, "scan_deadline_candidate_page", _raise)
    with pytest.raises(HTTPException) as exc_info:
        api.us_deadline_candidate_page(after_serial="bad")
    assert exc_info.value.status_code == 422


def test_candidate_page_refuses_lossy_truncation(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "scan_deadline_candidate_page",
        lambda **_kwargs: {
            "result_truncated": True,
            "candidates": [],
        },
    )
    with pytest.raises(HTTPException) as exc_info:
        api.us_deadline_candidate_page()
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "US_DEADLINE_CANDIDATE_BUFFER_EXCEEDED"
