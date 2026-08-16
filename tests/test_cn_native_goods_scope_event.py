from __future__ import annotations

import uuid

import pytest

from app.cn import native_goods_scope_event
from app.cn.native_goods_scope_event import (
    NativeGoodsScopeEventCutoverClient,
    NativeGoodsScopeEventExecutor,
    goods_scope_event_sql,
)
from app.cn.storage_v2_events import EventBaselineDeltaClient


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
            raise RuntimeError("synthetic native goods-scope-event failure")
        return len(self.commands)


class _Delegate:
    final_tasks_executed = 3
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


def _legacy_goods_placeholder(package: str, source_rank: int = 100) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            if(cur.application_number = '', 'GOODS_SCOPE_OBSERVED',
               'GOODS_SCOPE_CHANGED_OBSERVED')
        FROM markorbit_facts.cn_stage_scope_publish AS incoming
        LEFT JOIN markorbit_facts.cn_case_scope_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.class_no = incoming.class_no
        WHERE (cur.application_number = '' OR cur.source_rank < {source_rank})
          AND (cur.application_number = '' OR cur.scope_hash != incoming.scope_hash)
          AND incoming.package_id = toUUID('{package}')
    """


def test_goods_scope_event_sql_preserves_storage_v2_delta_and_legacy_hash() -> None:
    package = uuid.uuid4()
    sql = goods_scope_event_sql(
        package,
        package_kind="MONTHLY_PATCH",
        source_rank=500,
        lower="A100",
        upper="A200",
    )
    assert "'GOODS_SCOPE_CHANGED_OBSERVED'" in sql
    assert "'GOODS_SCOPE_OBSERVED'" not in sql
    assert "'GOODS'" in sql
    assert "toNullable(incoming.class_no)" in sql
    assert "'goods_scope'" in sql
    assert "'mapping_version', incoming.goods_status_mapping_version" in sql
    assert "FROM markorbit_facts.cn_case_scope_current FINAL" in sql
    assert "WHERE (application_number, class_no) IN" in sql
    assert "cur.source_rank < 500" in sql
    assert "cur.scope_hash != incoming.scope_hash" in sql
    assert "application_number >= 'A100'" in sql
    assert "application_number < 'A200'" in sql
    assert "incoming.application_number, '|GOODS|'" in sql
    assert "cur.scope_hash, '|', incoming.scope_hash" in sql
    assert "toString(500)" in sql


def test_goods_scope_event_resume_skips_successful_range() -> None:
    package = uuid.uuid4()
    store = _Store()
    first_client = _Client(query_results=[[("A300",)], []], fail_command=2)
    first = NativeGoodsScopeEventExecutor(
        client=first_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_rank=100,
        subtask_store=store,
        target_rows=2,
    )
    with pytest.raises(RuntimeError, match=r"task=2/2.*synthetic native goods-scope-event failure"):
        first.execute()

    retry_client = _Client(query_results=[[("A300",)], []])
    retry = NativeGoodsScopeEventExecutor(
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
    assert len(retry_client.commands) == 1
    assert "application_number >= 'A300'" in retry_client.commands[0]


def test_storage_v2_outer_adapter_rewrites_then_native_goods_takes_over(monkeypatch) -> None:
    package = uuid.uuid4()
    store = _Store()
    delegate = _Delegate()
    execution = _Client(query_results=[[]])
    monkeypatch.setattr(
        native_goods_scope_event,
        "get_package",
        lambda package_id: {"package_kind": "MONTHLY_PATCH"},
    )
    native = NativeGoodsScopeEventCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=100,
        subtask_store=store,
        allow_new_cutover=True,
    )
    outer = EventBaselineDeltaClient(native)
    result = outer.command(_legacy_goods_placeholder(str(package)))
    outer.command(
        "INSERT INTO markorbit_facts.cn_observed_event "
        "SELECT 'DERIVED_CASE_OBSERVED' FROM markorbit_facts.cn_stage_case_publish"
    )
    outer.assert_rewrite_counts()
    summary = native.assert_final_publish_complete()

    assert native.native_goods_scope_event_enabled is True
    assert result.range_count == 1
    assert len(execution.commands) == 1
    assert delegate.commands == []
    assert delegate.final_assertions == 1
    assert outer.goods_rewrite_count == 1
    assert outer.derived_skip_count == 1
    assert summary["FAILED"] == 0


def test_old_checkpoint_without_marker_keeps_legacy_goods_scope_event() -> None:
    package = uuid.uuid4()
    delegate = _Delegate()
    client = NativeGoodsScopeEventCutoverClient(
        delegate,
        execution_client=_Client(),
        package_uuid=package,
        source_rank=100,
        subtask_store=_Store(),
        allow_new_cutover=False,
    )
    assert client.native_goods_scope_event_enabled is False
    client.command(_legacy_goods_placeholder(str(package)))
    client.assert_final_publish_complete()
    assert len(delegate.commands) == 1
