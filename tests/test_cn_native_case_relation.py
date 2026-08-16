from __future__ import annotations

from datetime import date
import uuid

import pytest

from app.cn import native_case_relation, native_case_scope_current
from app.cn.native_case_relation import (
    NativeCaseRelationCutoverClient,
    NativeCaseRelationExecutor,
    case_relation_current_sql,
)


class _QueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class _ExecutionClient:
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
            raise RuntimeError("synthetic native relation failure")
        return len(self.commands)


class _Delegate:
    def __init__(self):
        self.commands: list[str] = []
        self.final_tasks_executed = 5
        self.final_tasks_skipped = 2
        self.final_assertions = 0

    def command(self, sql: str, *args, **kwargs):
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
        if not row or row["sql_hash"] != sql_hash:
            return None
        return row["status"]

    def mark_running(self, *, task_key, sql_hash, **metadata):
        self.rows[task_key] = {"status": "RUNNING", "sql_hash": sql_hash, **metadata}

    def mark_success(self, task_key):
        self.rows[task_key]["status"] = "SUCCESS"

    def mark_failed(self, task_key, error):
        self.rows[task_key]["status"] = "FAILED"
        self.rows[task_key]["error"] = error


def _case_scope_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        SELECT incoming.case_id
        FROM markorbit_facts.cn_stage_scope_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def _relation_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_relation_current
        SELECT incoming.relation_id
        FROM markorbit_facts.cn_stage_case_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
          AND incoming.is_derived_case = 1
    """


def _scope_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_scope_carve_out_current
        SELECT generateUUIDv4()
        FROM markorbit_facts.cn_stage_scope_publish
        WHERE package_id = toUUID('{package}')
    """


def test_native_relation_sql_preserves_structural_semantics_and_bounds_range() -> None:
    package = uuid.uuid4()
    sql = case_relation_current_sql(
        package,
        package_kind="MONTHLY_PATCH",
        source_rank=777,
        lower="R100",
        upper="R200",
    )

    assert "INSERT INTO markorbit_facts.cn_case_relation_current" in sql
    assert "FROM markorbit_facts.cn_stage_case_publish AS incoming" in sql
    assert "incoming.is_derived_case = 1" in sql
    assert "incoming.application_number >= 'R100'" in sql
    assert "incoming.application_number < 'R200'" in sql
    assert "'DERIVED_CASE', 'UNKNOWN', incoming.filing_route" in sql
    assert "'SUFFIX_AND_ROOT_NUMBER_OBSERVED'" in sql
    assert "'|DERIVED_CASE|'" in sql
    assert f"toUUID('{package}')" in sql
    assert "'MONTHLY_PATCH'" in sql
    assert "777, now64(3), 0" in sql


def test_native_relation_resumes_only_failed_application_range() -> None:
    package = uuid.uuid4()
    store = _Store()
    first_client = _ExecutionClient(query_results=[[("R300",)], []], fail_command=2)
    first = NativeCaseRelationExecutor(
        client=first_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_rank=11,
        subtask_store=store,
        target_rows=2,
    )

    with pytest.raises(RuntimeError, match=r"task=2/2.*synthetic native relation failure"):
        first.execute()

    assert len(first_client.commands) == 2
    statuses = [row["status"] for row in store.rows.values()]
    assert statuses.count("SUCCESS") == 1
    assert statuses.count("FAILED") == 1

    retry_client = _ExecutionClient(query_results=[[("R300",)], []])
    retry = NativeCaseRelationExecutor(
        client=retry_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_rank=11,
        subtask_store=store,
        target_rows=2,
    )
    result = retry.execute()

    assert result.range_count == 2
    assert result.skipped == 1
    assert result.executed == 1
    assert len(retry_client.commands) == 1
    assert "incoming.application_number >= 'R300'" in retry_client.commands[0]


def test_fresh_relation_stack_runs_case_scope_relation_then_carve_out(monkeypatch) -> None:
    package = uuid.uuid4()
    store = _Store()
    delegate = _Delegate()
    execution = _ExecutionClient(query_results=[[], [], []])
    monkeypatch.setattr(
        native_case_relation,
        "get_package",
        lambda package_id: {"package_kind": "MONTHLY_PATCH"},
    )
    monkeypatch.setattr(
        native_case_scope_current,
        "get_package",
        lambda package_id: {
            "package_kind": "MONTHLY_PATCH",
            "source_period_end": date(2026, 1, 31),
        },
    )
    client = NativeCaseRelationCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=22,
        subtask_store=store,
        allow_new_cutover=True,
        target_rows=2,
    )

    client.command(_case_scope_placeholder(str(package)))
    client.command(_relation_placeholder(str(package)))
    client.command(_scope_placeholder(str(package)))
    summary = client.assert_final_publish_complete()

    assert delegate.commands == []
    assert len(execution.commands) == 3
    assert "INSERT INTO markorbit_facts.cn_case_scope_current" in execution.commands[0]
    assert "FROM markorbit_facts.cn_stage_case_publish AS incoming" in execution.commands[1]
    assert "INSERT INTO markorbit_facts.cn_scope_carve_out_current" in execution.commands[2]
    assert delegate.final_assertions == 1
    assert summary["FAILED"] == 0
    assert client.final_tasks_executed == 8
    assert client.final_tasks_skipped == 2


def test_preexisting_checkpoint_without_relation_marker_keeps_legacy_relation() -> None:
    package = uuid.uuid4()
    delegate = _Delegate()
    execution = _ExecutionClient()
    client = NativeCaseRelationCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=22,
        subtask_store=_Store(),
        allow_new_cutover=False,
    )

    assert client.native_case_relation_enabled is False
    client.command(_relation_placeholder(str(package)))
    client.assert_final_publish_complete()

    assert len(delegate.commands) == 1
    assert execution.commands == []
    assert delegate.final_assertions == 1


def test_native_relation_final_assertion_fails_closed_if_placeholder_missing() -> None:
    package = uuid.uuid4()
    client = NativeCaseRelationCutoverClient(
        _Delegate(),
        execution_client=_ExecutionClient(),
        package_uuid=package,
        source_rank=22,
        subtask_store=_Store(),
        allow_new_cutover=True,
    )

    with pytest.raises(RuntimeError, match="enabled but never observed"):
        client.assert_final_publish_complete()
