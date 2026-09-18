from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cn.entity_trademark_portfolio import (
    EntityPortfolioInvalid,
    EntityPortfolioRequest,
    EntityPortfolioUnavailable,
    READY_VERSION,
    execute_page,
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


ENTITY_ID = "20000000-0000-0000-0000-000000000002"
READINESS_COLUMNS = [
    "ready_version",
    "source_watermark",
    "source_max_rank",
    "implementation_sha",
    "accepted_at",
]
PORTFOLIO_COLUMNS = [
    "role",
    "application_number",
    "has_current",
    "has_former",
    "relation_count",
    "first_observed_at",
    "last_observed_at",
    "latest_source_rank",
    "latest_source_package_id",
    "latest_event_hash",
]


def _ready() -> SimpleNamespace:
    return _result(
        READINESS_COLUMNS,
        [
            (
                READY_VERSION,
                "cn:watermark:123",
                123,
                "a" * 40,
                datetime(2026, 9, 18, 1, 0, 0),
            )
        ],
    )


def _portfolio_row(
    application: str,
    *,
    role: str = "AGENT",
    current: int = 1,
    former: int = 0,
    rank: int = 10,
) -> tuple[object, ...]:
    return (
        role,
        application,
        current,
        former,
        1,
        datetime(2025, 1, 1, 0, 0, 0),
        datetime(2026, 1, 1, 0, 0, 0),
        rank,
        "30000000-0000-0000-0000-000000000001",
        f"{rank:064x}",
    )


def _case_rows(*applications: str) -> SimpleNamespace:
    return _result(
        ["application_number", "mark_name_raw", "classes"],
        [(item, f"MARK-{item}", [9, 35]) for item in applications],
    )


def test_entity_portfolio_reads_current_and_historical_states() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(
                PORTFOLIO_COLUMNS,
                [
                    _portfolio_row("10000001", current=1, former=1, rank=11),
                    _portfolio_row("10000002", current=0, former=1, rank=12),
                ],
            ),
            _case_rows("10000001", "10000002"),
        ]
    )

    page = execute_page(
        EntityPortfolioRequest(entity_id=ENTITY_ID, role="AGENT", scope="all", page_size=10),
        client=client,
        runtime_engine_version="M1.7",
    )

    assert [item["application_number"] for item in page["results"]] == ["10000001", "10000002"]
    assert page["results"][0]["relationship_states"] == ["CURRENT_AGENT", "FORMER_AGENT"]
    assert page["results"][1]["relationship_states"] == ["FORMER_AGENT"]
    assert page["results"][0]["mark_name_raw"] == "MARK-10000001"
    query = client.queries[1][0]
    assert "entity_id = toUUID" in query
    assert "source_rank <= 123" in query
    assert "role = 'AGENT'" in query
    assert "GROUP BY role, application_number, relation_key" in query
    assert "action = 'SUPERSEDED'" in query
    assert "(has_current = 1 OR has_former = 1)" in query
    assert "ORDER BY role, application_number" in query


def test_entity_portfolio_cursor_is_snapshot_and_query_bound() -> None:
    first = FakeClient(
        [
            _ready(),
            _result(
                PORTFOLIO_COLUMNS,
                [
                    _portfolio_row("10000001", rank=11),
                    _portfolio_row("10000002", rank=12),
                ],
            ),
            _case_rows("10000001"),
        ]
    )
    first_page = execute_page(
        EntityPortfolioRequest(entity_id=ENTITY_ID, role="AGENT", scope="current", page_size=1),
        client=first,
        runtime_engine_version="M1.7",
    )
    assert first_page["next_cursor"]
    second = FakeClient(
        [
            _ready(),
            _result(PORTFOLIO_COLUMNS, [_portfolio_row("10000002", rank=12)]),
            _case_rows("10000002"),
        ]
    )
    second_page = execute_page(
        EntityPortfolioRequest(
            entity_id=ENTITY_ID,
            role="AGENT",
            scope="current",
            page_size=1,
            cursor=first_page["next_cursor"],
        ),
        client=second,
        runtime_engine_version="M1.7",
    )
    assert second_page["results"][0]["application_number"] == "10000002"
    assert "tuple(role, application_number) > tuple('AGENT', '10000001')" in second.queries[1][0]


def test_entity_portfolio_fails_closed_until_ready() -> None:
    client = FakeClient(
        [
            _result(
                READINESS_COLUMNS,
                [
                    (
                        "CN_ENTITY_TRADEMARK_PORTFOLIO_SCHEMA_V1",
                        "",
                        0,
                        "a" * 40,
                        datetime(2026, 9, 18),
                    )
                ],
            )
        ]
    )
    with pytest.raises(EntityPortfolioUnavailable, match="not backfilled and accepted"):
        execute_page(EntityPortfolioRequest(entity_id=ENTITY_ID), client=client)


def test_entity_portfolio_validates_input() -> None:
    with pytest.raises(EntityPortfolioInvalid):
        EntityPortfolioRequest(entity_id="not-a-uuid")
    with pytest.raises(EntityPortfolioInvalid):
        EntityPortfolioRequest(entity_id=ENTITY_ID, role="ATTORNEY")
    with pytest.raises(EntityPortfolioInvalid):
        EntityPortfolioRequest(entity_id=ENTITY_ID, scope="former")


def test_entity_portfolio_schema_is_entity_keyed_and_incremental() -> None:
    sql = Path("database/clickhouse/init/020_cn_entity_trademark_portfolio.sql").read_text(
        encoding="utf-8"
    )
    assert (
        "CREATE TABLE IF NOT EXISTS markorbit_facts.cn_entity_trademark_relationship_event" in sql
    )
    assert "cn_entity_trademark_relationship_event_mv" in sql
    assert "ORDER BY (entity_id, role, application_number, relation_key, event_hash)" in sql
    assert "FROM markorbit_facts.cn_observed_event" in sql
    assert "cn_entity_trademark_portfolio_readiness" in sql
    assert "CN_ENTITY_TRADEMARK_PORTFOLIO_SCHEMA_V1" in sql
    assert "INSERT INTO markorbit_facts.cn_entity_trademark_relationship_event\nSELECT" not in sql
