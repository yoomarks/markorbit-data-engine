from datetime import date

import app.us_assignment.api as api


def test_serial_assignment_api_has_no_legal_title_conclusion(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_assignments_for_serial",
        lambda serial, limit: [
            {
                "reel_frame_id": "1234/0056",
                "recorded_date": date(2026, 8, 1),
                "conveyance_text": "ASSIGNS THE ENTIRE INTEREST",
            }
        ],
    )
    result = api.us_assignments_for_serial("88991234", 100)
    assert result["assignment_count"] == 1
    assert result["legal_ownership_conclusion"] is False
    assert "NOT_LEGAL_TITLE_CONCLUSION" in result["semantics"]


def test_reconciliation_is_only_normalized_exact_name_set(monkeypatch) -> None:
    responses = iter(
        [
            [{"party_name": "Beta Brand Inc."}],
            [{"party_name": " beta   brand inc. "}],
        ]
    )
    monkeypatch.setattr(api, "_query", lambda _sql: next(responses))
    monkeypatch.setattr(
        api,
        "_assignments_for_serial",
        lambda _serial, _limit: [
            {
                "source_package_id": "00000000-0000-0000-0000-000000000001",
                "reel_frame_id": "1234/0056",
                "recorded_date": date(2026, 8, 1),
                "conveyance_text": "ASSIGNS THE ENTIRE INTEREST",
            }
        ],
    )
    result = api.us_assignment_owner_reconciliation("88991234")
    assert result["comparison"] == "MATCH"
    assert result["comparison_method"] == "WHITESPACE_AND_CASE_NORMALIZED_EXACT_NAME_SET_ONLY"
    assert result["legal_ownership_conclusion"] is False


def test_reconciliation_does_not_equate_different_names(monkeypatch) -> None:
    responses = iter(
        [[{"party_name": "Current Owner LLC"}], [{"party_name": "Recorded Assignee Inc."}]]
    )
    monkeypatch.setattr(api, "_query", lambda _sql: next(responses))
    monkeypatch.setattr(
        api,
        "_assignments_for_serial",
        lambda _serial, _limit: [
            {
                "source_package_id": "00000000-0000-0000-0000-000000000001",
                "reel_frame_id": "1/1",
                "recorded_date": date(2026, 8, 1),
                "conveyance_text": "CHANGE OF NAME",
            }
        ],
    )
    result = api.us_assignment_owner_reconciliation("88991234")
    assert result["comparison"] == "DIFFER"
    assert "does not determine legal title" in result["warning"]
