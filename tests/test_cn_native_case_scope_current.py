from __future__ import annotations

from datetime import date
import uuid

import pytest

from app.cn import native_case_scope_current
from app.cn.native_case_scope_current import (
    NativeCaseScopeCutoverClient,
    NativeCaseScopeExecutor,
    case_scope_current_sql,
)


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _Client:
    def __init__(self, query_results=None, *, fail_command: int | None = None):
        self.query_results = [list(rows) for rows in (query_results or [])]
        self.queries: list[str] = []
        self.commands: list[str] = []
        self.fail_command = fail_command

    def query(self, sql, *args, **kwargs):
        self.queries.append(sql)
        rows = self.query_results.pop(0) if self.query_results else []
        return _Result(rows)

    def command(self, sql, *args, **kwargs):
        self.commands.append(sql)
        if self.fail_command is not None and len(self.commands) == self.fail_command:
            raise RuntimeError("synthetic native case-scope failure")
        return len(self.commands)


class _Delegate:
    final_tasks_executed = 4
    final_tasks_skipped = 3

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


def _placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        SELECT incoming.case_id
        FROM markorbit_facts.cn_stage_scope_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def test_native_case_scope_sql_preserves_current_semantics_and_exact_key_filter() -> None:
    package = uuid.uuid4()
    sql = case_scope_current_sql(
        package,
        package_kind="MONTHLY_PATCH",
        source_effective_date=date(2026, 1, 31),
        source_rank=500,
        lower="A100",
        upper="A200",
    )

    assert "INSERT INTO markorbit_facts.cn_case_scope_current" in sql
    assert "FROM markorbit_facts.cn_stage_scope_publish" in sql
    assert "application_number >= 'A100'" in sql
    assert "application_number < 'A200'" in sql
    assert "FROM markorbit_facts.cn_case_scope_current FINAL" in sql
    assert "WHERE (application_number, class_no) IN" in sql
    assert "cur.application_number = '' OR cur.source_rank <= 500" in sql
    assert "'MONTHLY_PATCH'" in sql
    assert "toDate32('2026-01-31')" in sql
    assert f"toUUID('{package}')" in sql


def test_native_case_scope_nullable_effective_date_matches_legacy() -> None:
    sql = case_scope_current_sql(
        uuid.uuid4(),
        package_kind="FULL_SNAPSHOT",
        source_effective_date=None,
        source_rank=1,
        lower=None,
        upper=None,
    )
    assert "CAST(NULL, 'Nullable(Date32)')" in sql


def test_native_case_scope_resume_skips_successful_range() -> None:
    package = uuid.uuid4()
    store = _Store()
    first_client = _Client(query_results=[[("A300",)], []], fail_command=2)
    executor = NativeCaseScopeExecutor(
        client=first_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_effective_date=date(2026, 1, 31),
        source_rank=100,
        subtask_store=store,
        target_rows=2,
    )
    with pytest.raises(RuntimeError, match=r"task=2/2.*synthetic native case-scope failure"):
        executor.execute()

    retry_client = _Client(query_results=[[("A300",)], []])
    retry = NativeCaseScopeExecutor(
        client=retry_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_effective_date=date(2026, 1, 31),
        source_rank=100,
        subtask_store=store,
        target_rows=2,
    )
    result = retry.execute()
    assert result.range_count == 2
    assert result.skipped == 1
    assert result.executed == 1
    assert len(retry_client.commands) == 1
    assert "application_number >= 'A300'" in retry_client.commands[0]


def test_fresh_case_scope_cutover_bypasses_delegate(monkeypatch) -> None:
    package = uuid.uuid4()
    store = _Store()
    delegate = _Delegate()
    execution = _Client(query_results=[[]])
    monkeypatch.setattr(
        native_case_scope_current,
        "get_package",
        lambda package_id: {
            "package_kind": "MONTHLY_PATCH",
            "source_period_end": date(2026, 1, 31),
        },
    )
    client = NativeCaseScopeCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=100,
        subtask_store=store,
        allow_new_cutover=True,
    )

    client.command(_placeholder(str(package)))
    summary = client.assert_final_publish_complete()
    assert client.native_case_scope_enabled is True
    assert delegate.commands == []
    assert len(execution.commands) == 1
    assert delegate.final_assertions == 1
    assert summary["FAILED"] == 0
    assert client.final_tasks_executed == 5
    assert client.final_tasks_skipped == 3


def test_old_checkpoint_without_marker_keeps_legacy_case_scope() -> None:
    package = uuid.uuid4()
    delegate = _Delegate()
    execution = _Client()
    client = NativeCaseScopeCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=100,
        subtask_store=_Store(),
        allow_new_cutover=False,
    )
    assert client.native_case_scope_enabled is False
    client.command(_placeholder(str(package)))
    client.assert_final_publish_complete()
    assert len(delegate.commands) == 1
    assert execution.commands == []
