from __future__ import annotations

from datetime import date

import pytest

import app.us_ttab.read_model as read_model


def test_serial_lookup_bounds_latest_state_to_linked_proceedings(monkeypatch) -> None:
    package_current = "00000000-0000-0000-0000-000000000002"
    package_old = "00000000-0000-0000-0000-000000000001"
    responses = iter(
        [
            [
                {
                    "proceeding_number": "91230001",
                    "property_versions": [
                        (package_current, "PLAINTIFF", "123", "LIVE", "700", "MARK")
                    ],
                },
                {
                    "proceeding_number": "91230002",
                    "property_versions": [
                        (package_old, "DEFENDANT", "456", "LIVE", "700", "OTHER")
                    ],
                },
            ],
            [
                {
                    "proceeding_number": "91230001",
                    "source_package_id": package_current,
                    "source_rank": 2,
                    "filing_date": date(2026, 8, 2),
                },
                {
                    "proceeding_number": "91230002",
                    "source_package_id": package_current,
                    "source_rank": 2,
                    "filing_date": date(2026, 8, 3),
                },
            ],
        ]
    )
    queries: list[str] = []

    def fake_rows(sql: str, **kwargs):
        queries.append(sql)
        assert kwargs["settings"]["max_rows_to_read"] == 1_000_000
        return next(responses)

    monkeypatch.setattr(read_model, "_rows", fake_rows)

    result = read_model.proceedings_for_serial("88991234")

    assert [row["proceeding_number"] for row in result] == ["91230001"]
    assert result[0]["party_side"] == "PLAINTIFF"
    assert "GROUP BY proceeding_number" in queries[0]
    assert "us_ttab_proceeding_history" not in queries[0]
    assert "WHERE proceeding_number IN ('91230001', '91230002')" in queries[1]
    assert "GROUP BY proceeding_number" not in queries[1]


def test_serial_lookup_fails_closed_above_candidate_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(
        read_model,
        "_rows",
        lambda _sql, **_kwargs: [
            {"proceeding_number": str(index), "property_versions": []}
            for index in range(read_model.MAX_SERIAL_CANDIDATES + 1)
        ],
    )

    with pytest.raises(read_model.TTABQueryScopeExceeded):
        read_model.proceedings_for_serial("88991234")
