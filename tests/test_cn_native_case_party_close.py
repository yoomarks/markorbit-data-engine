from __future__ import annotations

from datetime import date
import uuid

import pytest

from app.cn import native_case_party_close
from app.cn.native_case_party_close import (
    NativeCasePartyCloseCutoverClient,
    NativeCasePartyCloseExecutor,
    case_party_close_sql,
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
            raise RuntimeError("synthetic native party-close failure")
        return len(self.commands)


class _Delegate:
    final_tasks_executed = 4
    final_tasks_skipped = 2

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


def _close_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_party_current
        SELECT incoming.relation_id, 'SUPERSEDED_BY_SOURCE_OBSERVATION'
        FROM markorbit_facts.cn_stage_party_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def _current_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_party_current
        SELECT incoming.relation_id, 'OBSERVED_CURRENT'
        FROM markorbit_facts.cn_stage_party_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def test_native_party_close_sql_preserves_touch_and_replacement_semantics() -> None:
    package = uuid.uuid4()
    sql = case_party_close_sql(
        package,
        package_kind="MONTHLY_PATCH",
        source_effective_date=date(2026, 1, 31),
        source_rank=500,
        lower="A100",
        upper="A200",
    )

    assert "INSERT INTO markorbit_facts.cn_case_party_current" in sql
    assert "WITH touched AS" in sql
    assert "GROUP BY application_number, role" in sql
    assert "argMin(source_file, source_first_line) AS touched_source_file" in sql
    assert "FROM markorbit_facts.cn_case_party_current FINAL" in sql
    assert "WHERE (application_number, role) IN" in sql
    assert "LEFT JOIN incoming_keys AS incoming" in sql
    assert "incoming.relation_key = cur.relation_key" in sql
    assert "cur.is_current = 1" in sql
    assert "cur.source_rank < 500" in sql
    assert "incoming.application_number = ''" in sql
    assert "'SUPERSEDED_BY_SOURCE_OBSERVATION'" in sql
    assert "'CASE_ROLE_REPLACE'" in sql
    assert "cur.valid_from, toDate32('2026-01-31')" in sql
    assert "cur.source_row_hash" in sql
    assert "cur.record_hash, '|SUPERSEDED|'" in sql
    assert "application_number >= 'A100'" in sql
    assert "application_number < 'A200'" in sql


def test_native_party_close_resume_skips_successful_range() -> None:
    package = uuid.uuid4()
    store = _Store()
    first_client = _Client(query_results=[[("A300",)], []], fail_command=2)
    first = NativeCasePartyCloseExecutor(
        client=first_client,
        package_uuid=package,
        package_kind="MONTHLY_PATCH",
        source_effective_date=date(2026, 1, 31),
        source_rank=100,
        subtask_store=store,
        target_rows=2,
    )
    with pytest.raises(RuntimeError, match=r"task=2/2.*synthetic native party-close failure"):
        first.execute()

    retry_client = _Client(query_results=[[("A300",)], []])
    retry = NativeCasePartyCloseExecutor(
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


def test_fresh_close_cutover_only_intercepts_close(monkeypatch) -> None:
    package = uuid.uuid4()
    store = _Store()
    delegate = _Delegate()
    execution = _Client(query_results=[[]])
    monkeypatch.setattr(
        native_case_party_close,
        "get_package",
        lambda package_id: {
            "package_kind": "MONTHLY_PATCH",
            "source_period_end": date(2026, 1, 31),
        },
    )
    client = NativeCasePartyCloseCutoverClient(
        delegate,
        execution_client=execution,
        package_uuid=package,
        source_rank=100,
        subtask_store=store,
        allow_new_cutover=True,
    )

    client.command(_current_placeholder(str(package)))
    client.command(_close_placeholder(str(package)))
    summary = client.assert_final_publish_complete()

    assert client.native_case_party_close_enabled is True
    assert len(delegate.commands) == 1
    assert "'OBSERVED_CURRENT'" in delegate.commands[0]
    assert len(execution.commands) == 1
    assert "SUPERSEDED_BY_SOURCE_OBSERVATION" in execution.commands[0]
    assert delegate.final_assertions == 1
    assert summary["FAILED"] == 0


def test_old_checkpoint_without_marker_keeps_legacy_close() -> None:
    package = uuid.uuid4()
    delegate = _Delegate()
    client = NativeCasePartyCloseCutoverClient(
        delegate,
        execution_client=_Client(),
        package_uuid=package,
        source_rank=100,
        subtask_store=_Store(),
        allow_new_cutover=False,
    )
    assert client.native_case_party_close_enabled is False
    client.command(_close_placeholder(str(package)))
    client.assert_final_publish_complete()
    assert len(delegate.commands) == 1
