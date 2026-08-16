from __future__ import annotations

import uuid

import pytest

from app.cn import native_exclusive_term_event
from app.cn.native_exclusive_term_event import (
    NativeExclusiveTermEventCutoverClient,
    NativeExclusiveTermEventExecutor,
    exclusive_term_event_sql,
)


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _Client:
    def __init__(self, query_results=None, *, fail_command: int | None = None):
        self.query_results = [list(rows) for rows in (query_results or [])]
        self.commands: list[str] = []
        self.fail_command = fail_command

    def query(self, sql, *args, **kwargs):
        rows = self.query_results.pop(0) if self.query_results else []
        return _Result(rows)

    def command(self, sql, *args, **kwargs):
        self.commands.append(sql)
        if self.fail_command is not None and len(self.commands) == self.fail_command:
            raise RuntimeError("synthetic exclusive-term failure")
        return len(self.commands)


class _Delegate:
    final_tasks_executed = 2
    final_tasks_skipped = 1

    def __init__(self):
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql, *args, **kwargs):
        self.commands.append(sql)
        return len(self.commands)

    def assert_final_publish_complete(self):
        self.final_assertions += 1
        return {"SUCCESS": 1, "RUNNING": 0, "FAILED": 0}


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
        return None if not row or row["sql_hash"] != sql_hash else row["status"]

    def mark_running(self, *, task_key, sql_hash, **metadata):
        self.rows[task_key] = {"status": "RUNNING", "sql_hash": sql_hash, **metadata}

    def mark_success(self, task_key):
        self.rows[task_key]["status"] = "SUCCESS"

    def mark_failed(self, task_key, error):
        self.rows[task_key]["status"] = "FAILED"
        self.rows[task_key]["error"] = error


def _placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT 'TERM_EXTENDED_OBSERVED'
        FROM markorbit_facts.cn_stage_case_publish
        WHERE package_id = toUUID('{package}')
    """


def test_exclusive_term_sql_preserves_dynamic_type_payload_and_range() -> None:
    package = uuid.uuid4()
    sql = exclusive_term_event_sql(
        package,
        package_kind="MONTHLY_PATCH",
        source_rank=700,
        lower="A100",
        upper="A200",
    )
    assert "'TERM_EXTENDED_OBSERVED', 'EXCLUSIVE_TERM_OBSERVED'" in sql
    assert "incoming.exclusive_end_date > cur.valid_until" in sql
    assert "incoming.exclusive_end_date" in sql
    assert "'exclusive_term'" in sql
    assert "toJSONString(map('from'" in sql
    assert "'raw', incoming.exclusive_period" in sql
    assert "cur.source_rank < 700" in sql
    assert "incoming.exclusive_start_date IS NOT NULL OR incoming.exclusive_end_date IS NOT NULL" in sql
    assert "cur.valid_from" in sql and "cur.valid_until" in sql
    assert "application_number >= 'A100'" in sql
    assert "application_number < 'A200'" in sql


def test_exclusive_term_resume_skips_successful_range() -> None:
    package = uuid.uuid4()
    store = _Store()
    first_client = _Client(query_results=[[("A300",)], []], fail_command=2)
    first = NativeExclusiveTermEventExecutor(
        client=first_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_rank=100,
        subtask_store=store,
        target_rows=2,
    )
    with pytest.raises(RuntimeError, match=r"task=2/2.*synthetic exclusive-term failure"):
        first.execute()

    retry_client = _Client(query_results=[[("A300",)], []])
    retry = NativeExclusiveTermEventExecutor(
        client=retry_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_rank=100,
        subtask_store=store,
        target_rows=2,
    )
    result = retry.execute()
    assert result.range_count == 2
    assert result.skipped == 1
    assert result.executed == 1


def test_exclusive_term_cutover_bypasses_delegate(monkeypatch) -> None:
    package = uuid.uuid4()
    store = _Store()
    delegate = _Delegate()
    execution = _Client(query_results=[[]])
    monkeypatch.setattr(
        native_exclusive_term_event,
        "get_package",
        lambda package_id: {"package_kind": "MONTHLY_PATCH"},
    )
    client = NativeExclusiveTermEventCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=100,
        subtask_store=store,
        allow_new_cutover=True,
    )
    client.command(_placeholder(str(package)))
    client.assert_final_publish_complete()
    assert delegate.commands == []
    assert len(execution.commands) == 1
    assert delegate.final_assertions == 1


def test_old_checkpoint_without_marker_keeps_legacy_exclusive_term() -> None:
    package = uuid.uuid4()
    delegate = _Delegate()
    client = NativeExclusiveTermEventCutoverClient(
        delegate,
        execution_client=_Client(),
        package_uuid=package,
        source_rank=100,
        subtask_store=_Store(),
        allow_new_cutover=False,
    )
    assert client.native_exclusive_term_event_enabled is False
    client.command(_placeholder(str(package)))
    client.assert_final_publish_complete()
    assert len(delegate.commands) == 1
