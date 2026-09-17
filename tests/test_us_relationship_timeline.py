from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

import app.us.relationship_timeline as timeline


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = iter(responses)
        self.queries: list[tuple[str, dict[str, object]]] = []

    def query(self, sql: str, *, settings: dict[str, object]):
        self.queries.append((sql, settings))
        return next(self.responses)


def _result(columns: list[str], rows: list[tuple[object, ...]]) -> SimpleNamespace:
    return SimpleNamespace(column_names=columns, result_rows=rows)


COMMON_COLUMNS = [
    "observation_key",
    "reel_frame_id",
    "record_hash",
    "source_file",
    "source_package_id",
    "observed_at",
]


def _assignment_record() -> dict[str, object]:
    return {
        "reel_frame_id": "1234/0056",
        "source_package_id": "00000000-0000-0000-0000-000000000001",
        "recorded_date": date(2026, 8, 1),
        "conveyance_text": "ASSIGNS THE ENTIRE INTEREST",
    }


def test_relationship_timeline_maps_recorded_assignment_and_ttab_facts(monkeypatch) -> None:
    monkeypatch.setattr(
        timeline,
        "_assignments_for_serial",
        lambda _serial, _limit, **_kwargs: [_assignment_record()],
    )
    monkeypatch.setattr(
        timeline,
        "proceedings_for_serial",
        lambda _serial, _limit, **_kwargs: [
            {
                "proceeding_number": "91301803",
                "proceeding_type_code": "OPP",
                "source_package_id": "00000000-0000-0000-0000-000000000002",
                "filing_date": date(2025, 9, 18),
            }
        ],
    )
    assignment_common = (
        "a" * 64,
        "1234/0056",
        "b" * 64,
        "assignment.xml",
        "00000000-0000-0000-0000-000000000001",
        datetime(2026, 8, 2, 12, 0),
    )
    ttab_common = (
        "c" * 64,
        "91301803",
        "d" * 64,
        "ttab.xml",
        "00000000-0000-0000-0000-000000000002",
        datetime(2026, 2, 3, 12, 0),
    )
    client = FakeClient(
        [
            _result(
                COMMON_COLUMNS
                + [
                    "property_key",
                    "ordinal",
                    "serial_number",
                    "registration_number",
                    "international_registration_number",
                ],
                [assignment_common + ("e" * 64, 1, "99026361", "7000001", "")],
            ),
            _result(
                ["relationship_type"]
                + COMMON_COLUMNS
                + [
                    "party_key",
                    "ordinal",
                    "party_name",
                    "execution_date",
                ],
                [
                    ("ASSIGNOR",)
                    + assignment_common
                    + (
                        "f" * 64,
                        1,
                        "Assignor LLC",
                        date(2026, 7, 31),
                    ),
                    ("ASSIGNEE",)
                    + assignment_common
                    + (
                        "1" * 64,
                        1,
                        "Assignee Inc.",
                        None,
                    ),
                ],
            ),
            _result(
                [
                    "fact_kind",
                    "observation_key",
                    "proceeding_number",
                    "record_hash",
                    "source_file",
                    "source_package_id",
                    "observed_at",
                    "fact_key",
                    "side",
                    "party_name",
                    "party_id",
                    "role",
                    "registration_number",
                ],
                [
                    ("PROPERTY",) + ttab_common + ("2" * 64, "DEFENDANT", "", "", "", "7000001"),
                    ("PARTY",)
                    + ttab_common
                    + (
                        "3" * 64,
                        "PLAINTIFF",
                        "Opposer LLC",
                        "P1",
                        "Plaintiff",
                        "",
                    ),
                ],
            ),
        ]
    )

    result = timeline.relationships_for_trademark(client, "99026361")

    assert result["relationship_count"] == 5
    by_type = {item["edge"]["relationship_type"]: item for item in result["relationships"]}
    assert set(by_type) == {
        "ASSIGNMENT_PROPERTY",
        "ASSIGNOR",
        "ASSIGNEE",
        "PROCEEDING_PROPERTY",
        "OPPOSITION_PLAINTIFF",
    }
    assert by_type["ASSIGNOR"]["edge"]["temporal"]["event_at"] == "2026-08-01"
    assert by_type["ASSIGNOR"]["edge"]["temporal"]["is_current"] is False
    assert (
        by_type["OPPOSITION_PLAINTIFF"]["edge"]["provenance"]["authority_level"]
        == "DIRECT_OFFICIAL"
    )
    assert by_type["ASSIGNMENT_PROPERTY"]["edge"]["target"] == {
        "resource_type": "ASSIGNMENT",
        "resource_id": "us:assignment:1234/0056",
    }
    assert all(settings["max_rows_to_read"] == 1_000_000 for _, settings in client.queries)
    assert all("AS source_package_id" not in sql for sql, _ in client.queries)


def test_current_scope_does_not_infer_live_recorded_relationships(monkeypatch) -> None:
    monkeypatch.setattr(timeline, "_assignments_for_serial", lambda _serial, _limit, **_kwargs: [])
    monkeypatch.setattr(timeline, "proceedings_for_serial", lambda _serial, _limit, **_kwargs: [])

    result = timeline.relationships_for_trademark(FakeClient([]), "99026361", scope="current")

    assert result["relationship_count"] == 0
    assert result["current_relationship_inference"] is False


def test_unmapped_ttab_party_role_is_counted_not_invented(monkeypatch) -> None:
    monkeypatch.setattr(timeline, "_assignments_for_serial", lambda _serial, _limit, **_kwargs: [])
    monkeypatch.setattr(
        timeline,
        "proceedings_for_serial",
        lambda _serial, _limit, **_kwargs: [
            {
                "proceeding_number": "90000001",
                "proceeding_type_code": "EXT",
                "source_package_id": "00000000-0000-0000-0000-000000000002",
                "filing_date": date(2026, 1, 1),
            }
        ],
    )
    common = (
        "a" * 64,
        "90000001",
        "b" * 64,
        "ttab.xml",
        "00000000-0000-0000-0000-000000000002",
        datetime(2026, 1, 2),
    )
    client = FakeClient(
        [
            _result(
                [
                    "fact_kind",
                    "observation_key",
                    "proceeding_number",
                    "record_hash",
                    "source_file",
                    "source_package_id",
                    "observed_at",
                    "fact_key",
                    "side",
                    "party_name",
                    "party_id",
                    "role",
                    "registration_number",
                ],
                [("PARTY",) + common + ("c" * 64, "PLAINTIFF", "Requester", "P1", "Plaintiff", "")],
            ),
        ]
    )

    result = timeline.relationships_for_trademark(client, "99026361")

    assert result["relationship_count"] == 0
    assert result["unmapped_ttab_party_count"] == 1


def test_invalid_scope_and_edge_ceiling_fail_closed(monkeypatch) -> None:
    with pytest.raises(timeline.USRelationshipTimelineInvalid):
        timeline.relationships_for_trademark(FakeClient([]), "99026361", scope="former")

    monkeypatch.setattr(
        timeline,
        "_assignment_items",
        lambda _client, _serial: [{"edge": {}}] * (timeline.MAX_RELATIONSHIP_EDGES + 1),
    )
    monkeypatch.setattr(timeline, "_ttab_items", lambda _client, _serial: ([], 0))
    with pytest.raises(timeline.USRelationshipTimelineScopeExceeded):
        timeline.relationships_for_trademark(FakeClient([]), "99026361")
