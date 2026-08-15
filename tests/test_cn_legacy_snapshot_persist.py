from __future__ import annotations

import uuid

import pytest

from app.cn.legacy_snapshot_persist import (
    LegacySnapshotPersistClient,
    plan_agent_code_batches,
    plan_application_ranges,
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


class _SequencedClient(_FakeClient):
    def __init__(self, query_results):
        super().__init__()
        self.query_results = [list(rows) for rows in query_results]

    def query(self, sql: str, *args, **kwargs):
        self.queries.append(sql)
        rows = self.query_results.pop(0) if self.query_results else []
        return _QueryResult(rows)


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


def _priority_insert(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_priority_current
        SELECT
            application_number,
            class_no,
            priority_number,
            groupArray(toString(row_hash))
        FROM markorbit_facts.cn_stage_priority
        WHERE package_id = toUUID('{package}')
        GROUP BY application_number, class_no, priority_number
    """


def _madrid_insert(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_madrid_current
        SELECT
            application_number,
            international_registration_number,
            groupArray(toString(row_hash))
        FROM markorbit_facts.cn_stage_madrid
        WHERE package_id = toUUID('{package}')
        GROUP BY application_number, international_registration_number
    """


def _mark_aux_complete(client: LegacySnapshotPersistClient, package: str) -> None:
    client.command(_priority_insert(package))
    client.command(_madrid_insert(package))


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


def test_application_range_plan_uses_half_open_whole_application_boundaries() -> None:
    package = uuid.uuid4()
    client = _SequencedClient(
        [
            [("200",)],
            [("200",)],
            [("300",)],
            [],
        ]
    )

    ranges = plan_application_ranges(
        package,
        client=client,
        stage_table="cn_stage_priority",
        target_rows=2,
    )

    assert ranges == [(None, "200"), ("200", "300"), ("300", None)]
    assert "LIMIT 1 OFFSET 2" in client.queries[0]
    assert "application_number >= '200'" in client.queries[1]
    assert "application_number > '200'" in client.queries[2]
    assert "application_number >= '300'" in client.queries[3]


def test_application_range_plan_rejects_unapproved_stage_table() -> None:
    with pytest.raises(ValueError, match="unsupported application stage table"):
        plan_application_ranges(
            uuid.uuid4(),
            client=_FakeClient(),
            stage_table="cn_stage_basic",
        )


def test_agent_current_insert_is_split_with_both_stage_sides_bounded() -> None:
    package = uuid.uuid4()
    delegate = _FakeClient()
    client = LegacySnapshotPersistClient(
        delegate,
        package_uuid=package,
        agent_batches=[("A", "B"), ("C",)],
        priority_ranges=[],
        madrid_ranges=[],
    )

    result = client.command(_agent_insert(str(package)))
    _mark_aux_complete(client, str(package))
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
        priority_ranges=[],
        madrid_ranges=[],
    )

    assert client.command(_agent_insert(str(package))) is None
    _mark_aux_complete(client, str(package))
    client.assert_agent_persist_complete()
    assert delegate.commands == []


def test_priority_insert_is_split_by_application_range() -> None:
    package = uuid.uuid4()
    delegate = _FakeClient()
    client = LegacySnapshotPersistClient(
        delegate,
        package_uuid=package,
        agent_batches=[],
        priority_ranges=[(None, "200"), ("200", None)],
        madrid_ranges=[],
    )

    result = client.command(_priority_insert(str(package)))

    assert result == 2
    assert client.priority_chunk_count == 2
    assert client.physical_priority_commands == 2
    assert "application_number < '200'" in delegate.commands[0]
    assert "application_number >= '200'" in delegate.commands[1]
    for sql in delegate.commands:
        assert "FROM (\n            SELECT *\n            FROM markorbit_facts.cn_stage_priority" in sql
        assert sql.count(f"package_id = toUUID('{package}')") >= 2


def test_madrid_insert_is_split_by_application_range() -> None:
    package = uuid.uuid4()
    delegate = _FakeClient()
    client = LegacySnapshotPersistClient(
        delegate,
        package_uuid=package,
        agent_batches=[],
        priority_ranges=[],
        madrid_ranges=[(None, "G200"), ("G200", None)],
    )

    result = client.command(_madrid_insert(str(package)))

    assert result == 2
    assert client.madrid_chunk_count == 2
    assert client.physical_madrid_commands == 2
    assert "application_number < 'G200'" in delegate.commands[0]
    assert "application_number >= 'G200'" in delegate.commands[1]
    for sql in delegate.commands:
        assert "FROM (\n            SELECT *\n            FROM markorbit_facts.cn_stage_madrid" in sql


def test_priority_ranges_are_planned_lazily_at_the_priority_command() -> None:
    package = uuid.uuid4()
    delegate = _SequencedClient(
        [
            [("200",)],
            [],
        ]
    )
    client = LegacySnapshotPersistClient(
        delegate,
        package_uuid=package,
        agent_batches=[],
        madrid_ranges=[],
    )

    assert client.priority_chunk_count == 0
    result = client.command(_priority_insert(str(package)))

    assert result == 2
    assert client.priority_chunk_count == 2
    assert len(delegate.queries) == 2
    assert "FROM markorbit_facts.cn_stage_priority" in delegate.queries[0]


def test_auxiliary_persistence_assertion_detects_missing_legacy_insert() -> None:
    client = LegacySnapshotPersistClient(
        _FakeClient(),
        package_uuid=uuid.uuid4(),
        agent_batches=[],
        priority_ranges=[],
        madrid_ranges=[],
    )

    with pytest.raises(RuntimeError, match="cn_priority_current"):
        client.assert_aux_persist_complete()


def test_non_agent_snapshot_failure_gets_precise_subphase() -> None:
    package = uuid.uuid4()

    class _FailingClient(_FakeClient):
        def command(self, sql: str, *args, **kwargs):
            raise RuntimeError("boom")

    client = LegacySnapshotPersistClient(
        _FailingClient(),
        package_uuid=package,
        agent_batches=[],
        priority_ranges=[],
        madrid_ranges=[],
    )

    with pytest.raises(RuntimeError, match="legacy_snapshot_subphase=CASE_SCOPE_CURRENT"):
        client.command("INSERT INTO markorbit_facts.cn_case_scope_current SELECT 1")
