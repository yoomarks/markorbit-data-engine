from __future__ import annotations

import subprocess

import pytest

from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    QueryRows,
    TARGET_DISTRO,
    WslNativeClickHouseClient,
)
from app.us.target_canary_stage2_reconcile import (
    RECONCILE_DECISION,
    reconcile_prepared_state,
)


def _prepared_journal() -> dict[str, object]:
    return {
        "state": "PREPARED",
        "stage": {"status": "NOT_STARTED", "row_counts": None},
        "commits": {
            table: {
                "status": "PENDING",
                "expected_rows": None,
                "observed_rows": None,
            }
            for table in APPLICATION_CANARY_TABLES
        },
    }


class FakeClient:
    def __init__(self, *, target_tables: list[str] | None = None, stage_tables: int = 0, app_parts: int = 0, warm_parts: int = 0, table_rows: int = 0) -> None:
        self.target_tables = target_tables or []
        self.stage_tables = stage_tables
        self.app_parts = app_parts
        self.warm_parts = warm_parts
        self.table_rows = table_rows

    def query(self, sql: str) -> QueryRows:
        if "FROM system.tables" in sql and "database='markorbit_facts'" in sql and "SELECT name" in sql:
            return QueryRows([[name] for name in self.target_tables])
        if "FROM system.tables" in sql and "database='markorbit_canary_stage'" in sql:
            return QueryRows([[self.stage_tables]])
        if "FROM system.parts" in sql and "database='markorbit_facts'" in sql:
            return QueryRows([[self.app_parts]])
        if "FROM system.parts" in sql and "disk_name='warm_cn'" in sql:
            return QueryRows([[self.warm_parts]])
        if sql.startswith("SELECT count() FROM markorbit_facts."):
            return QueryRows([[self.table_rows]])
        raise AssertionError(f"unexpected SQL: {sql}")


def test_wsl_native_client_uses_explicit_exec_for_backtick_ddl() -> None:
    calls: list[list[str]] = []

    def runner(args, *, input, text, capture_output, check):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    client = WslNativeClickHouseClient(runner=runner)
    ddl = "CREATE TABLE markorbit_facts.t (`case_id` String) ENGINE=Memory"
    client.command(ddl)

    args = calls[0]
    assert args[:5] == ["wsl.exe", "-d", TARGET_DISTRO, "-u", "root"]
    assert args[5:8] == ["--exec", "clickhouse", "client"]
    assert "--" not in args[:8]
    assert args[args.index("--query") + 1] == ddl
    assert "`case_id`" in args[-1]


def test_prepared_reconciliation_accepts_schema_only_empty_target() -> None:
    journal = _prepared_journal()
    first_short = APPLICATION_CANARY_TABLES[0].split(".", 1)[1]
    result = reconcile_prepared_state(journal, FakeClient(target_tables=[first_short]))  # type: ignore[arg-type]

    assert result["decision"] == RECONCILE_DECISION
    assert result["journal_state"] == "PREPARED"
    assert result["stage_status"] == "NOT_STARTED"
    assert result["existing_application_tables"] == 1
    assert result["target_rows"] == 0
    assert result["stage_tables"] == 0
    assert result["application_active_parts"] == 0
    assert result["warm_cn_active_parts"] == 0
    assert result["safe_to_resume"] is True


def test_prepared_reconciliation_blocks_if_stage_or_data_started() -> None:
    journal = _prepared_journal()
    journal["stage"]["status"] = "STARTED"  # type: ignore[index]
    with pytest.raises(RuntimeError, match="stage has already started"):
        reconcile_prepared_state(journal, FakeClient())  # type: ignore[arg-type]

    journal = _prepared_journal()
    first_short = APPLICATION_CANARY_TABLES[0].split(".", 1)[1]
    with pytest.raises(RuntimeError, match="not empty"):
        reconcile_prepared_state(
            journal,
            FakeClient(target_tables=[first_short], table_rows=1),  # type: ignore[arg-type]
        )

    with pytest.raises(RuntimeError, match="stage tables"):
        reconcile_prepared_state(journal, FakeClient(stage_tables=1))  # type: ignore[arg-type]


def test_prepared_reconciliation_blocks_unexpected_target_table_or_parts() -> None:
    journal = _prepared_journal()
    with pytest.raises(RuntimeError, match="unexpected tables"):
        reconcile_prepared_state(journal, FakeClient(target_tables=["not_application"]))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="target active parts"):
        reconcile_prepared_state(journal, FakeClient(app_parts=1))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="warm_cn active parts"):
        reconcile_prepared_state(journal, FakeClient(warm_parts=1))  # type: ignore[arg-type]
