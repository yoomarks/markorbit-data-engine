from __future__ import annotations

import uuid

import pytest

from app.cn.native_agent_snapshot import (
    NativeAgentCutoverClient,
    NativeAgentSnapshotExecutor,
    agent_current_sql,
    native_agent_operation_hash,
)


class _ExecutionClient:
    def __init__(self, *, fail_command: int | None = None):
        self.commands: list[str] = []
        self.fail_command = fail_command

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        if self.fail_command is not None and len(self.commands) == self.fail_command:
            raise RuntimeError("synthetic native Agent failure")
        return len(self.commands)


class _Delegate:
    def __init__(self):
        self.commands: list[str] = []
        self.final_tasks_executed = 7
        self.final_tasks_skipped = 3
        self.aux_assertions = 0
        self.legacy_agent_assertions = 0

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        return len(self.commands)

    def assert_aux_persist_complete(self):
        self.aux_assertions += 1

    def assert_agent_persist_complete(self):
        self.legacy_agent_assertions += 1


class _Store:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    @staticmethod
    def task_key(*, sql_hash, stage_table, lower, upper):
        return f"{sql_hash}:{stage_table}:{lower}:{upper}"

    def is_success(self, task_key, sql_hash):
        row = self.rows.get(task_key)
        return bool(row and row["status"] == "SUCCESS" and row["sql_hash"] == sql_hash)

    def task_status(self, task_key, sql_hash):
        row = self.rows.get(task_key)
        if not row or row["sql_hash"] != sql_hash:
            return None
        return row["status"]

    def mark_running(self, *, task_key, sql_hash, **metadata):
        self.rows[task_key] = {
            "status": "RUNNING",
            "sql_hash": sql_hash,
            **metadata,
        }

    def mark_success(self, task_key):
        self.rows[task_key]["status"] = "SUCCESS"

    def mark_failed(self, task_key, error):
        self.rows[task_key]["status"] = "FAILED"
        self.rows[task_key]["error"] = error


def _agent_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_agent_current
        SELECT b.agent_code
        FROM markorbit_facts.cn_stage_basic AS b
        LEFT JOIN markorbit_facts.cn_stage_agent AS a
          ON a.package_id = b.package_id AND a.agent_code = b.agent_code
        WHERE b.package_id = toUUID('{package}')
    """


def test_native_agent_sql_bounds_both_stage_sides_to_complete_batch() -> None:
    package = uuid.uuid4()
    sql = agent_current_sql(
        package,
        source_rank=321,
        agent_codes=("A001", "A002"),
    )

    assert "INSERT INTO markorbit_facts.cn_agent_current" in sql
    assert "FROM markorbit_facts.cn_stage_basic" in sql
    assert "FROM markorbit_facts.cn_stage_agent" in sql
    assert sql.count("agent_code IN ('A001', 'A002')") == 2
    assert "argMax(a.agent_name, toUInt64(a.source_start_line))" in sql
    assert "arraySort(groupArray(toString(b.row_hash)))" in sql
    assert f"toUUID('{package}'), 321, now64(3), 0" in sql


def test_native_agent_batch_identity_includes_complete_code_list() -> None:
    first = native_agent_operation_hash(("A001", "A003"))
    different_middle = native_agent_operation_hash(("A001", "A002", "A003"))

    assert first != different_middle
    assert first == native_agent_operation_hash(("A001", "A003"))


def test_native_agent_resumes_only_failed_batch() -> None:
    package = uuid.uuid4()
    store = _Store()
    batches = [("A001", "A002"), ("A003",)]

    first = NativeAgentSnapshotExecutor(
        client=_ExecutionClient(fail_command=2),
        package_uuid=package,
        source_rank=99,
        agent_batches=batches,
        subtask_store=store,
    )
    with pytest.raises(RuntimeError, match=r"batch=2/2.*synthetic native Agent failure"):
        first.execute()

    assert [row["status"] for row in store.rows.values()].count("SUCCESS") == 1
    assert [row["status"] for row in store.rows.values()].count("FAILED") == 1

    retry_client = _ExecutionClient()
    retry = NativeAgentSnapshotExecutor(
        client=retry_client,
        package_uuid=package,
        source_rank=99,
        agent_batches=batches,
        subtask_store=store,
    )
    result = retry.execute()

    assert result.skipped == 1
    assert result.executed == 1
    assert len(retry_client.commands) == 1
    assert "agent_code IN ('A003')" in retry_client.commands[0]
    assert all(row["status"] == "SUCCESS" for row in store.rows.values())


def test_new_checkpoint_cuts_agent_over_and_bypasses_legacy_delegate() -> None:
    package = uuid.uuid4()
    store = _Store()
    delegate = _Delegate()
    execution = _ExecutionClient()
    client = NativeAgentCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=55,
        agent_batches=[("A001",)],
        subtask_store=store,
        allow_new_cutover=True,
    )

    assert client.native_agent_enabled is True
    client.command(_agent_placeholder(str(package)))
    client.assert_agent_persist_complete()

    assert delegate.commands == []
    assert len(execution.commands) == 1
    assert "agent_code IN ('A001')" in execution.commands[0]
    assert delegate.aux_assertions == 1
    assert delegate.legacy_agent_assertions == 0
    assert client.final_tasks_executed == 8
    assert client.final_tasks_skipped == 3


def test_preexisting_checkpoint_without_agent_marker_keeps_legacy_agent() -> None:
    package = uuid.uuid4()
    store = _Store()
    delegate = _Delegate()
    execution = _ExecutionClient()
    client = NativeAgentCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=55,
        agent_batches=[("A001",)],
        subtask_store=store,
        allow_new_cutover=False,
    )

    assert client.native_agent_enabled is False
    client.command(_agent_placeholder(str(package)))
    client.assert_agent_persist_complete()

    assert len(delegate.commands) == 1
    assert execution.commands == []
    assert delegate.legacy_agent_assertions == 1


def test_existing_agent_marker_keeps_native_mode_on_resume() -> None:
    package = uuid.uuid4()
    store = _Store()

    first = NativeAgentCutoverClient(
        _Delegate(),
        execution_client=_ExecutionClient(),
        package_uuid=package,
        source_rank=55,
        agent_batches=[("A001",)],
        subtask_store=store,
        allow_new_cutover=True,
    )
    assert first.native_agent_enabled is True

    resumed = NativeAgentCutoverClient(
        _Delegate(),
        execution_client=_ExecutionClient(),
        package_uuid=package,
        source_rank=55,
        agent_batches=[("A001",)],
        subtask_store=store,
        allow_new_cutover=False,
    )
    assert resumed.native_agent_enabled is True
