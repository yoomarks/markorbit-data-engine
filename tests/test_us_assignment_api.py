from datetime import date

import pytest

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


def test_serial_assignment_lookup_bounds_latest_state_to_linked_records(monkeypatch) -> None:
    package_current = "00000000-0000-0000-0000-000000000002"
    package_old = "00000000-0000-0000-0000-000000000001"
    responses = iter(
        [
            [
                {"reel_frame_id": "100/0001", "package_ids": [package_current]},
                {"reel_frame_id": "200/0002", "package_ids": [package_old]},
            ],
            [
                {
                    "reel_frame_id": "100/0001",
                    "source_package_id": package_current,
                    "source_rank": 2,
                    "recorded_date": date(2026, 8, 2),
                },
                {
                    "reel_frame_id": "200/0002",
                    "source_package_id": package_current,
                    "source_rank": 2,
                    "recorded_date": date(2026, 8, 3),
                },
            ],
        ]
    )
    queries: list[str] = []

    def fake_query(sql: str, **kwargs):
        queries.append(sql)
        assert kwargs["settings"]["max_rows_to_read"] == 1_000_000
        return next(responses)

    monkeypatch.setattr(api, "_query", fake_query)

    result = api._assignments_for_serial("88991234", 100)

    assert [row["reel_frame_id"] for row in result] == ["100/0001"]
    assert "GROUP BY reel_frame_id" in queries[0]
    assert "us_assignment_record_history" not in queries[0]
    assert "WHERE reel_frame_id IN ('100/0001', '200/0002')" in queries[1]
    assert "GROUP BY reel_frame_id" not in queries[1]


def test_serial_assignment_lookup_fails_closed_above_candidate_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_query",
        lambda _sql, **_kwargs: [
            {"reel_frame_id": str(index), "package_ids": [str(index)]}
            for index in range(api.MAX_SERIAL_CANDIDATES + 1)
        ],
    )

    with pytest.raises(api.HTTPException) as exc_info:
        api._assignments_for_serial("88991234", 100)

    assert exc_info.value.detail["code"] == "US_ASSIGNMENT_QUERY_SCOPE_EXCEEDED"


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
