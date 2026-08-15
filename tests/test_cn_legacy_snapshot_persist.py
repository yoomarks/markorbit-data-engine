from __future__ import annotations

import uuid

import pytest

from app.cn.legacy_snapshot_persist import (
    LegacySnapshotPersistClient,
    plan_agent_code_batches,
)


class _QueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeClient:
    def __init__(self, query_rows=None):
        self.query_rows = list(query_rows or [])
        self.queries: list[str] = []
        self.commands: list[str] = []

    def query(self, sql: str, *args, **kwargs):
        self.queries.append(sql)
        return _QueryResult(self.query_rows)

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        return len(self.commands)


def _agent_insert(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_agent_current
        SELECT
            b.agent_code,
            groupArray(toString(b.row_hash))
        FROM markorbit_facts.cn_stage_basic AS b
        LEFT JOIN markorbit_facts.cn_stage_agent AS a
          ON a.package_id = b.package_id AND a.agent_code = b.agent_code
        WHERE b.package_id = toUUID('{package}') AND b.agent_code != ''
        GROUP BY b.agent_code
    """


def test_agent_batch_plan_keeps_each_agent_whole() -> None:
    package = uuid.uuid4()
    client = _FakeClient(
        [
            ("A", 70_000),
            ("B", 40_000),
            ("C", 20_000),
            ("D", 1),
        ]
    )

    batches = plan_agent_code_batches(
        package,
        client=client,
        target_rows=100_000,
        max_codes=10,
    )

    assert batches == [("A",), ("B", "C", "D")]
    assert len(client.queries) == 1
    assert "GROUP BY agent_code" in client.queries[0]
    assert "groupArray" not in client.queries[0]


def test_agent_batch_plan_caps_code_count_even_when_rows_are_sparse() -> None:
    package = uuid.uuid4()
    client = _FakeClient([(f"A{index}", 1) for index in range(5)])

    batches = plan_agent_code_batches(
        package,
        client=client,
        target_rows=100_000,
        max_codes=2,
    )

    assert batches == [("A0", "A1"), ("A2", "A3"), ("A4",)]


def test_agent_current_insert_is_split_with_both_stage_sides_bounded() -> None:
    package = uuid.uuid4()
    delegate = _FakeClient()
    client = LegacySnapshotPersistClient(
        delegate,
        package_uuid=package,
        agent_batches=[("A", "B"), ("C",)],
    )

    result = client.command(_agent_insert(str(package)))
    client.assert_agent_persist_complete()

    assert result == 2
    assert client.agent_chunk_count == 2
    assert client.agent_code_count == 3
    assert client.physical_agent_commands == 2
    assert len(delegate.commands) == 2
    assert "agent_code IN ('A', 'B')" in delegate.commands[0]
    assert "agent_code IN ('C')" in delegate.commands[1]
    for sql in delegate.commands:
        assert "FROM (\n            SELECT *\n            FROM markorbit_facts.cn_stage_basic" in sql
        assert "LEFT JOIN (\n            SELECT *\n            FROM markorbit_facts.cn_stage_agent" in sql
        assert sql.count(f"package_id = toUUID('{package}')") >= 3


def test_no_agent_rows_skip_the_whole_stage_agent_insert() -> None:
    package = uuid.uuid4()
    delegate = _FakeClient()
    client = LegacySnapshotPersistClient(
        delegate,
        package_uuid=package,
        agent_batches=[],
    )

    assert client.command(_agent_insert(str(package))) is None
    client.assert_agent_persist_complete()
    assert delegate.commands == []


def test_non_agent_snapshot_failure_gets_precise_subphase() -> None:
    package = uuid.uuid4()

    class _FailingClient(_FakeClient):
        def command(self, sql: str, *args, **kwargs):
            raise RuntimeError("boom")

    client = LegacySnapshotPersistClient(
        _FailingClient(),
        package_uuid=package,
        agent_batches=[],
    )

    with pytest.raises(RuntimeError, match="legacy_snapshot_subphase=CASE_SCOPE_CURRENT"):
        client.command("INSERT INTO markorbit_facts.cn_case_scope_current SELECT 1")
