from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.cn.agent_exact_read import (
    AgentExactReadInvalid,
    AgentExactReadUnavailable,
    READ_SETTINGS,
    read_agent_exact,
)


class FakeClient:
    def __init__(self, result: SimpleNamespace | Exception) -> None:
        self.result = result
        self.queries: list[tuple[str, dict[str, object]]] = []

    def query(self, sql: str, *, settings: dict[str, object]):
        self.queries.append((sql, settings))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _result(rows: list[tuple[object, ...]]) -> SimpleNamespace:
    return SimpleNamespace(
        column_names=[
            "agent_code",
            "mention_id",
            "entity_id",
            "agent_name",
            "agent_name_norm",
            "source_file",
            "source_first_line",
            "source_last_line",
            "source_row_hash",
            "source_package_id",
            "source_rank",
            "ingested_at",
        ],
        result_rows=rows,
    )


def test_exact_agent_code_returns_current_source_reference_and_entity_pointer() -> None:
    client = FakeClient(
        _result(
            [
                (
                    "A001",
                    "10000000-0000-0000-0000-000000000001",
                    "20000000-0000-0000-0000-000000000002",
                    "Example IP Agency",
                    "example ip agency",
                    "agents.csv",
                    10,
                    10,
                    bytes("a" * 64, "ascii"),
                    "30000000-0000-0000-0000-000000000003",
                    42,
                    datetime(2026, 9, 21, 1, 2, 3),
                )
            ]
        )
    )

    payload = read_agent_exact(client, " A001 ")

    assert payload["agent_code"] == "A001"
    assert payload["record"]["entity_id"] == "20000000-0000-0000-0000-000000000002"
    assert payload["record"]["source_reference"] == {
        "owner": "DATA_ENGINE",
        "kind": "CN_AGENT",
        "id": "A001",
        "version": "42",
        "fingerprintSha256": "a" * 64,
        "observedAt": "2026-09-21T01:02:03.000Z",
    }
    assert payload["record"]["currentness"]["state"] == "CURRENT_SOURCE_FACT"
    assert payload["record"]["legal_identity_verified"] is False
    assert payload["record"]["customer_relationship_established"] is False
    sql, settings = client.queries[0]
    assert "FROM markorbit_facts.cn_agent_current FINAL" in sql
    assert "WHERE agent_code = 'A001'" in sql
    assert "is_deleted = 0" in sql
    assert "LIMIT 2" in sql
    assert settings == READ_SETTINGS


def test_exact_agent_code_preserves_not_found_without_coverage_claim() -> None:
    payload = read_agent_exact(FakeClient(_result([])), "A404")
    assert payload == {
        "agent_code": "A404",
        "record": None,
        "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_CODE_FACT_NO_IDENTITY_RESOLUTION",
    }


def test_exact_agent_code_fails_closed_for_ambiguous_current_rows() -> None:
    row = (
        "A001",
        "10000000-0000-0000-0000-000000000001",
        None,
        "Example",
        "example",
        "agents.csv",
        1,
        1,
        "b" * 64,
        "30000000-0000-0000-0000-000000000003",
        1,
        datetime(2026, 9, 21),
    )
    with pytest.raises(AgentExactReadUnavailable, match="multiple current rows"):
        read_agent_exact(FakeClient(_result([row, row])), "A001")


def test_exact_agent_code_validates_input_and_maps_storage_failure() -> None:
    with pytest.raises(AgentExactReadInvalid):
        read_agent_exact(FakeClient(_result([])), "")

    with pytest.raises(AgentExactReadUnavailable, match="offline"):
        read_agent_exact(FakeClient(RuntimeError("offline")), "A001")
