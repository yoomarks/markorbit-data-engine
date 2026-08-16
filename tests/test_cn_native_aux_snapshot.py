from __future__ import annotations

import uuid

import pytest

from app.cn.native_aux_snapshot import (
    NativeAuxSnapshotExecutor,
    madrid_current_sql,
    native_aux_operation_hash,
    priority_current_sql,
)


class _QueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class _Client:
    def __init__(self, query_results=None, *, fail_command: int | None = None):
        self.query_results = [list(rows) for rows in (query_results or [])]
        self.queries: list[str] = []
        self.commands: list[str] = []
        self.fail_command = fail_command

    def query(self, sql: str, *args, **kwargs):
        self.queries.append(sql)
        rows = self.query_results.pop(0) if self.query_results else []
        return _QueryResult(rows)

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        if self.fail_command is not None and len(self.commands) == self.fail_command:
            raise RuntimeError("synthetic native auxiliary failure")
        return len(self.commands)


class _MemoryStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    @staticmethod
    def task_key(*, sql_hash, stage_table, lower, upper):
        return f"{sql_hash}:{stage_table}:{lower}:{upper}"

    def is_success(self, task_key, sql_hash):
        row = self.rows.get(task_key)
        return bool(row and row["status"] == "SUCCESS" and row["sql_hash"] == sql_hash)

    def mark_running(self, *, task_key, sql_hash, **metadata):
        attempts = int(self.rows.get(task_key, {}).get("attempts", 0)) + 1
        self.rows[task_key] = {
            "status": "RUNNING",
            "sql_hash": sql_hash,
            "attempts": attempts,
            **metadata,
        }

    def mark_success(self, task_key):
        self.rows[task_key]["status"] = "SUCCESS"

    def mark_failed(self, task_key, error):
        self.rows[task_key]["status"] = "FAILED"
        self.rows[task_key]["error"] = error


def test_native_priority_sql_is_bounded_without_legacy_rewrite() -> None:
    package = uuid.uuid4()
    sql = priority_current_sql(
        package,
        source_rank=123,
        lower="P100",
        upper="P200",
    )

    assert "INSERT INTO markorbit_facts.cn_priority_current" in sql
    assert "FROM markorbit_facts.cn_stage_priority" in sql
    assert "application_number >= 'P100'" in sql
    assert "application_number < 'P200'" in sql
    assert f"toUUID('{package}'), 123, now64(3), 0" in sql
    assert "groupArray(toString(row_hash))" in sql


def test_native_madrid_sql_preserves_legacy_aggregate_semantics() -> None:
    package = uuid.uuid4()
    sql = madrid_current_sql(
        package,
        source_rank=456,
        lower=None,
        upper="G200",
    )

    assert "INSERT INTO markorbit_facts.cn_madrid_current" in sql
    assert "FROM markorbit_facts.cn_stage_madrid" in sql
    assert "application_number < 'G200'" in sql
    assert "argMax(international_registration_date" in sql
    assert "arraySort(groupArray(toString(row_hash)))" in sql
    assert f"toUUID('{package}'), 456, now64(3), 0" in sql


def test_native_priority_resumes_only_failed_range() -> None:
    package = uuid.uuid4()
    store = _MemoryStore()

    first_client = _Client(
        query_results=[[("P200",)], []],
        fail_command=2,
    )
    first = NativeAuxSnapshotExecutor(
        client=first_client,
        package_uuid=package,
        source_rank=99,
        subtask_store=store,
        target_rows=2,
    )

    with pytest.raises(RuntimeError, match=r"task=2/2.*synthetic native auxiliary failure"):
        first.execute("PRIORITY_CURRENT")

    assert len(first_client.commands) == 2
    statuses = [row["status"] for row in store.rows.values()]
    assert statuses.count("SUCCESS") == 1
    assert statuses.count("FAILED") == 1

    retry_client = _Client(query_results=[[("P200",)], []])
    retry = NativeAuxSnapshotExecutor(
        client=retry_client,
        package_uuid=package,
        source_rank=99,
        subtask_store=store,
        target_rows=2,
    )
    result = retry.execute("PRIORITY_CURRENT")

    assert result.skipped == 1
    assert result.executed == 1
    assert len(retry_client.commands) == 1
    assert "application_number >= 'P200'" in retry_client.commands[0]
    assert all(row["status"] == "SUCCESS" for row in store.rows.values())


def test_native_operation_identity_is_node_specific_and_versioned() -> None:
    priority_hash = native_aux_operation_hash("PRIORITY_CURRENT")
    madrid_hash = native_aux_operation_hash("MADRID_CURRENT")

    assert len(priority_hash) == 64
    assert len(madrid_hash) == 64
    assert priority_hash != madrid_hash
    assert priority_hash == native_aux_operation_hash("PRIORITY_CURRENT")
