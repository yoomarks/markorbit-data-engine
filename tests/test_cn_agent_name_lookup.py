from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cn.agent_name_lookup import (
    AGENT_NAME_LOOKUP_READY_VERSION,
    AgentNameLookupInvalid,
    AgentNameLookupScopeExceeded,
    AgentNameLookupUnavailable,
    agents_by_name,
    normalize_agent_name,
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
    return _result(["version"], [(AGENT_NAME_LOOKUP_READY_VERSION,)])


def test_agent_lookup_uses_exact_normalized_name_and_current_revalidation() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(["agent_code"], [("A100",)]),
            _result(["agent_code", "agent_name"], [("A100", "示例 代理事务所")]),
        ]
    )

    result = agents_by_name(client, " 示例 代理事务所 ")

    assert result["normalized_name"] == "示例代理事务所"
    assert result["candidate_count"] == 1
    assert result["match_count"] == 1
    candidate_query, settings = client.queries[1]
    assert "cn_agent_name_candidate_lookup FINAL" in candidate_query
    assert "normalized_name = '示例代理事务所'" in candidate_query
    assert "LIMIT 501" in candidate_query
    assert settings["max_rows_to_read"] == 1_000_000
    current_query, _settings = client.queries[2]
    assert "cn_agent_current FINAL" in current_query
    assert "agent_code IN ('A100')" in current_query
    assert "agent_name_norm = '示例代理事务所'" in current_query


def test_agent_lookup_fails_closed_until_backfill_is_accepted() -> None:
    client = FakeClient([_result(["version"], [("CN_AGENT_NAME_LOOKUP_SCHEMA_V1",)])])

    with pytest.raises(AgentNameLookupUnavailable, match="not backfilled and accepted"):
        agents_by_name(client, "示例代理事务所")

    assert len(client.queries) == 1


def test_agent_lookup_rejects_invalid_and_oversized_scopes() -> None:
    with pytest.raises(AgentNameLookupInvalid):
        agents_by_name(FakeClient([]), "---")

    client = FakeClient(
        [_ready(), _result(["agent_code"], [(str(index),) for index in range(501)])]
    )
    with pytest.raises(AgentNameLookupScopeExceeded):
        agents_by_name(client, "常见代理事务所")


def test_agent_lookup_does_not_return_stale_name_candidates() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(["agent_code"], [("A100",)]),
            _result(["agent_code", "agent_name"], []),
        ]
    )

    result = agents_by_name(client, "原代理事务所")

    assert result["candidate_count"] == 1
    assert result["match_count"] == 0


def test_agent_lookup_schema_is_name_ordered_and_future_writes_are_projected() -> None:
    sql = Path("database/clickhouse/init/018_cn_agent_name_lookup.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS markorbit_facts.cn_agent_name_candidate_lookup" in sql
    assert "ORDER BY (normalized_name, agent_code)" in sql
    assert "ReplacingMergeTree(source_rank, is_deleted)" in sql
    assert "cn_agent_name_candidates_from_agent_mv" in sql
    assert "FROM markorbit_facts.cn_agent_current" in sql
    assert "INSERT INTO markorbit_facts.cn_agent_name_candidate_lookup\nSELECT" not in sql


def test_normalization_reuses_cn_fact_normalization() -> None:
    assert normalize_agent_name(" 示例（代理）事务所 ") == "示例代理事务所"
