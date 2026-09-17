from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cn.relationship_timeline import (
    RELATIONSHIP_TIMELINE_READY_VERSION,
    RelationshipTimelineInvalid,
    RelationshipTimelineScopeExceeded,
    RelationshipTimelineUnavailable,
    relationships_for_trademark,
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
    return _result(["version"], [(RELATIONSHIP_TIMELINE_READY_VERSION,)])


EVENT_COLUMNS = [
    "event_id",
    "application_number",
    "event_type",
    "event_date",
    "observed_at",
    "field_name",
    "old_value_compact",
    "new_value_compact",
    "evidence_level",
    "source_package_id",
    "source_package_kind",
    "source_file",
    "source_first_line",
    "source_last_line",
    "source_row_hash",
    "source_rank",
    "event_hash",
]


def _event(
    event_type: str,
    *,
    role: str,
    relation_key: str,
    source_rank: int,
    entity_id: str = "",
) -> tuple[object, ...]:
    fact = {
        "name": f"{role} name",
        "address": "official address",
        "relation_key": relation_key,
    }
    if entity_id:
        fact["entity_id"] = entity_id
    superseded = "SUPERSEDED" in event_type
    return (
        f"00000000-0000-0000-0000-{source_rank:012d}",
        "12345678",
        event_type,
        date(2026, 1, source_rank),
        datetime(2026, 1, source_rank, 12, 0, 0),
        role.lower(),
        json.dumps(fact) if superseded else "",
        "" if superseded else json.dumps(fact),
        "OFFICIAL_DATA_RELATION_REPLACEMENT" if superseded else "OFFICIAL_FACT_OBSERVATION",
        f"10000000-0000-0000-0000-{source_rank:012d}",
        "CN_SNAPSHOT",
        "official.csv",
        source_rank,
        source_rank,
        f"{source_rank:064x}",
        source_rank,
        f"{source_rank + 100:064x}",
    )


def test_relationship_timeline_derives_current_and_former_edges() -> None:
    owner_key = "a" * 64
    agent_key = "b" * 64
    owner_observed = list(
        _event(
            "OWNER_RELATION_OBSERVED",
            role="OWNER",
            relation_key=owner_key,
            source_rank=1,
            entity_id="20000000-0000-0000-0000-000000000001",
        )
    )
    owner_observed[14] = bytes(f"{1:064x}", "ascii")
    owner_observed[16] = bytes(f"{101:064x}", "ascii")
    rows = [
        tuple(owner_observed),
        _event(
            "AGENT_RELATION_OBSERVED",
            role="AGENT",
            relation_key=agent_key,
            source_rank=2,
            entity_id="20000000-0000-0000-0000-000000000002",
        ),
        _event(
            "AGENT_RELATION_SUPERSEDED_OBSERVED",
            role="AGENT",
            relation_key=agent_key,
            source_rank=3,
        ),
    ]
    client = FakeClient([_ready(), _result(EVENT_COLUMNS, rows)])

    result = relationships_for_trademark(client, "12345678")

    assert result["relationship_count"] == 2
    by_type = {item["edge"]["relationship_type"]: item for item in result["relationships"]}
    owner = by_type["CURRENT_OWNER"]["edge"]
    assert owner["source"] == {
        "resource_type": "OWNER",
        "resource_id": "cn:entity:20000000-0000-0000-0000-000000000001",
    }
    assert owner["target"]["resource_id"] == "cn:trademark:12345678"
    assert owner["temporal"]["valid_from"] is None
    assert owner["temporal"]["valid_to"] is None
    assert owner["temporal"]["is_current"] is True
    assert owner["evidence"]["source_record_id"] == f"{101:064x}"
    assert owner["evidence"]["source_hash"] == f"{1:064x}"
    former = by_type["FORMER_AGENT"]["edge"]
    assert former["temporal"]["is_current"] is False
    assert former["provenance"]["authority_level"] == "DERIVED_FROM_OFFICIAL_HISTORY"
    assert former["provenance"]["derivation"]["identity"] == "CN_RELATIONSHIP_TIMELINE_V1"
    assert former["evidence"]["source_record_id"] == f"{103:064x}"
    query, settings = client.queries[1]
    assert "cn_trademark_relationship_event FINAL" in query
    assert "application_number = '12345678'" in query
    assert "LIMIT 5001" in query
    assert settings["max_rows_to_read"] == 1_000_000


def test_relationship_scope_filters_derived_edges() -> None:
    key = "a" * 64
    rows = [
        _event("OWNER_RELATION_OBSERVED", role="OWNER", relation_key=key, source_rank=1),
        _event(
            "OWNER_RELATION_SUPERSEDED_OBSERVED",
            role="OWNER",
            relation_key=key,
            source_rank=2,
        ),
    ]
    client = FakeClient([_ready(), _result(EVENT_COLUMNS, rows)])

    result = relationships_for_trademark(client, "12345678", scope="current")

    assert result["relationship_count"] == 0


def test_relationship_timeline_preserves_former_lifecycle_after_reactivation() -> None:
    key = "a" * 64
    rows = [
        _event("OWNER_RELATION_OBSERVED", role="OWNER", relation_key=key, source_rank=1),
        _event(
            "OWNER_RELATION_SUPERSEDED_OBSERVED",
            role="OWNER",
            relation_key=key,
            source_rank=2,
        ),
        _event("OWNER_RELATION_OBSERVED", role="OWNER", relation_key=key, source_rank=3),
    ]
    result = relationships_for_trademark(
        FakeClient([_ready(), _result(EVENT_COLUMNS, rows)]), "12345678"
    )

    assert sorted(
        item["edge"]["relationship_type"] for item in result["relationships"]
    ) == ["CURRENT_OWNER", "FORMER_OWNER"]


def test_relationship_timeline_fails_closed_for_readiness_scope_and_orphan_close() -> None:
    client = FakeClient([_result(["version"], [("CN_RELATIONSHIP_TIMELINE_SCHEMA_V1",)])])
    with pytest.raises(RelationshipTimelineUnavailable, match="not backfilled and accepted"):
        relationships_for_trademark(client, "12345678")

    with pytest.raises(RelationshipTimelineInvalid):
        relationships_for_trademark(FakeClient([]), "12345678", scope="former")

    orphan = _event(
        "OWNER_RELATION_SUPERSEDED_OBSERVED",
        role="OWNER",
        relation_key="a" * 64,
        source_rank=2,
    )
    with pytest.raises(RelationshipTimelineUnavailable, match="no retained observed event"):
        relationships_for_trademark(
            FakeClient([_ready(), _result(EVENT_COLUMNS, [orphan])]), "12345678"
        )


def test_relationship_timeline_rejects_more_than_event_ceiling() -> None:
    rows = [(str(index),) for index in range(5_001)]
    with pytest.raises(RelationshipTimelineScopeExceeded):
        relationships_for_trademark(
            FakeClient([_ready(), _result(["event_id"], rows)]), "12345678"
        )


def test_relationship_schema_is_application_ordered_without_implicit_backfill() -> None:
    sql = Path("database/clickhouse/init/019_cn_relationship_timeline.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS markorbit_facts.cn_trademark_relationship_event" in sql
    assert "ORDER BY (application_number, event_hash)" in sql
    assert "cn_trademark_relationship_event_mv" in sql
    assert "FROM markorbit_facts.cn_observed_event" in sql
    assert "INSERT INTO markorbit_facts.cn_trademark_relationship_event\nSELECT" not in sql
