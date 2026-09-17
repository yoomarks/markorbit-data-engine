from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.us.registration_lookup import (
    MAX_REGISTRATION_CANDIDATES,
    REGISTRATION_LOOKUP_READY_VERSION,
    RegistrationLookupInvalid,
    RegistrationLookupScopeExceeded,
    RegistrationLookupUnavailable,
    lookup_registration,
)


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = iter(responses)
        self.queries: list[tuple[str, dict[str, object]]] = []

    def query(self, sql: str, *, settings: dict[str, object]):
        self.queries.append((sql, settings))
        return next(self.responses)


def _result(columns: list[str], rows: list[tuple[object, ...]]) -> SimpleNamespace:
    return SimpleNamespace(column_names=columns, result_rows=rows)


def _ready() -> SimpleNamespace:
    return _result(["version"], [(REGISTRATION_LOOKUP_READY_VERSION,)])


def test_lookup_uses_registration_candidates_then_verifies_current_case() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(["serial_number"], [("90000001",), ("90000002",)]),
            _result(
                ["serial_number", "registration_number"],
                [("90000002", "7265548")],
            ),
        ]
    )

    result = lookup_registration(client, "7265548")

    assert result["candidate_count"] == 2
    assert result["match_count"] == 1
    assert result["trademarks"] == [{"serial_number": "90000002", "registration_number": "7265548"}]
    readiness_sql, _readiness_settings = client.queries[0]
    candidate_sql, candidate_settings = client.queries[1]
    current_sql, current_settings = client.queries[2]
    assert "schema_version FINAL" in readiness_sql
    assert "us_registration_candidate_lookup FINAL" in candidate_sql
    assert "registration_number = '7265548'" in candidate_sql
    assert "serial_number IN ('90000001', '90000002')" in current_sql
    assert "registration_number = '7265548'" in current_sql
    assert candidate_settings["max_rows_to_read"] == 1_000_000
    assert current_settings["max_rows_to_read"] == 1_000_000


def test_lookup_returns_not_found_without_reading_case_table() -> None:
    client = FakeClient([_ready(), _result(["serial_number"], [])])

    result = lookup_registration(client, "7265548")

    assert result["match_count"] == 0
    assert len(client.queries) == 2


def test_lookup_rejects_invalid_registration_number() -> None:
    with pytest.raises(RegistrationLookupInvalid):
        lookup_registration(FakeClient([]), "US-7265548")


def test_lookup_fails_closed_above_candidate_ceiling() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(
                ["serial_number"],
                [(str(index),) for index in range(MAX_REGISTRATION_CANDIDATES + 1)],
            ),
        ]
    )

    with pytest.raises(RegistrationLookupScopeExceeded):
        lookup_registration(client, "7265548")


def test_lookup_fails_closed_until_backfill_is_accepted() -> None:
    client = FakeClient([_result(["version"], [("US_REGISTRATION_CANDIDATE_LOOKUP_SCHEMA_V1",)])])

    with pytest.raises(RegistrationLookupUnavailable, match="not backfilled and accepted"):
        lookup_registration(client, "7265548")

    assert len(client.queries) == 1


def test_schema_is_registration_ordered_without_implicit_data_backfill() -> None:
    sql = Path("database/clickhouse/init/016_us_registration_candidate_lookup.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS markorbit_facts.us_registration_candidate_lookup" in sql
    assert "ORDER BY (registration_number, serial_number)" in sql
    assert "ReplacingMergeTree(source_rank)" in sql
    assert "INSERT INTO markorbit_facts.us_registration_candidate_lookup" not in sql
    assert "US_REGISTRATION_CANDIDATE_LOOKUP_SCHEMA_V1" in sql
