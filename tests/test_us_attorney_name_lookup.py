from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.us.attorney_name_lookup import (
    ATTORNEY_NAME_LOOKUP_READY_VERSION,
    AttorneyNameLookupInvalid,
    AttorneyNameLookupScopeExceeded,
    AttorneyNameLookupUnavailable,
    attorneys_by_name,
    normalize_attorney_name,
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
    return _result(["version"], [(ATTORNEY_NAME_LOOKUP_READY_VERSION,)])


def test_attorney_lookup_uses_exact_normalized_name_and_frozen_budget() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(
                ["serial_number", "correspondent_key"],
                [("90000001", "a" * 64)],
            ),
            _result(
                ["serial_number", "correspondent_key", "attorney_name"],
                [("90000001", "a" * 64, "Jane Q. Attorney")],
            ),
        ]
    )

    result = attorneys_by_name(client, "  JANE   Q. ATTORNEY ")

    assert result["normalized_name"] == "jane q. attorney"
    assert result["match_count"] == 1
    candidate_query, settings = client.queries[1]
    assert "us_attorney_name_candidate_lookup FINAL" in candidate_query
    assert "normalized_name = 'jane q. attorney'" in candidate_query
    assert "LIMIT 501" in candidate_query
    assert settings["max_rows_to_read"] == 1_000_000
    assert settings["max_result_rows"] == 501
    current_query, _settings = client.queries[2]
    assert "us_correspondent_current FINAL" in current_query
    assert "serial_number IN ('90000001')" in current_query
    assert "lowerUTF8(replaceRegexpAll" in current_query


def test_attorney_lookup_fails_closed_until_backfill_is_accepted() -> None:
    client = FakeClient([_result(["version"], [("US_ATTORNEY_NAME_LOOKUP_SCHEMA_V1",)])])

    with pytest.raises(AttorneyNameLookupUnavailable, match="not backfilled and accepted"):
        attorneys_by_name(client, "Jane Q. Attorney")

    assert len(client.queries) == 1


def test_attorney_lookup_rejects_invalid_and_oversized_scopes() -> None:
    with pytest.raises(AttorneyNameLookupInvalid):
        attorneys_by_name(FakeClient([]), "   ")

    client = FakeClient(
        [_ready(), _result(["serial_number"], [(str(index),) for index in range(501)])]
    )
    with pytest.raises(AttorneyNameLookupScopeExceeded):
        attorneys_by_name(client, "Common Attorney")


def test_attorney_lookup_does_not_return_stale_name_candidates() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(
                ["serial_number", "correspondent_key"],
                [("90000001", "a" * 64)],
            ),
            _result(["serial_number", "attorney_name"], []),
        ]
    )

    result = attorneys_by_name(client, "Former Attorney")

    assert result["candidate_count"] == 1
    assert result["match_count"] == 0
    assert result["matches"] == []


def test_attorney_lookup_schema_is_name_ordered_and_future_writes_are_projected() -> None:
    sql = Path("database/clickhouse/init/017_us_attorney_name_lookup.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS markorbit_facts.us_attorney_name_candidate_lookup" in sql
    assert "ORDER BY (normalized_name, serial_number, correspondent_key)" in sql
    assert "ReplacingMergeTree(source_rank, is_deleted)" in sql
    assert "us_attorney_name_candidates_from_correspondent_mv" in sql
    assert "FROM markorbit_facts.us_correspondent_current" in sql
    assert "INSERT INTO markorbit_facts.us_attorney_name_candidate_lookup\nSELECT" not in sql


def test_normalization_matches_projection_contract() -> None:
    assert normalize_attorney_name(" JANE\tQ.\nATTORNEY ") == "jane q. attorney"
