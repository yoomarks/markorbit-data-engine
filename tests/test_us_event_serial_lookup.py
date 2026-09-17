from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.us.event_serial_lookup import (
    EVENT_SERIAL_LOOKUP_READY_VERSION,
    EventSerialLookupInvalid,
    EventSerialLookupScopeExceeded,
    EventSerialLookupUnavailable,
    events_for_serial,
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
    return _result(["version"], [(EVENT_SERIAL_LOOKUP_READY_VERSION,)])


def test_events_use_serial_ordered_projection_and_frozen_budget() -> None:
    client = FakeClient(
        [
            _ready(),
            _result(
                ["event_key", "serial_number", "event_code"],
                [("a" * 64, "90000001", "DOCK")],
            ),
        ]
    )

    result = events_for_serial(client, "90000001", limit=20)

    assert result["event_count"] == 1
    query, settings = client.queries[1]
    assert "us_event_serial_history FINAL" in query
    assert "serial_number = '90000001'" in query
    assert "LIMIT 21" in query
    assert settings["max_rows_to_read"] == 1_000_000
    assert settings["max_result_rows"] == 5_001


def test_events_fail_closed_until_backfill_is_accepted() -> None:
    client = FakeClient([_result(["version"], [("US_EVENT_SERIAL_LOOKUP_SCHEMA_V1",)])])

    with pytest.raises(EventSerialLookupUnavailable, match="not backfilled and accepted"):
        events_for_serial(client, "90000001")

    assert len(client.queries) == 1


def test_events_reject_invalid_serial_and_scope() -> None:
    with pytest.raises(EventSerialLookupInvalid):
        events_for_serial(FakeClient([]), "9001")

    client = FakeClient([_ready(), _result(["event_key"], [(str(i),) for i in range(3)])])
    with pytest.raises(EventSerialLookupScopeExceeded):
        events_for_serial(client, "90000001", limit=2)


def test_schema_is_serial_ordered_without_implicit_backfill() -> None:
    sql = Path("database/clickhouse/init/014_serial_event_lookup.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS markorbit_facts.us_event_serial_history" in sql
    assert "ORDER BY (serial_number, event_key)" in sql
    assert "ReplacingMergeTree(source_rank)" in sql
    assert "INSERT INTO markorbit_facts.us_event_serial_history" not in sql
