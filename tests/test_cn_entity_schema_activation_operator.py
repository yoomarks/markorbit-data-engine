from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cn.entity_schema_activation_operator import (
    PLAN_VERSION,
    RELATIONSHIP_TIMELINE_READY_VERSION,
    SQL_RELATIVE_PATH,
    _sha256,
    _split_sql,
    apply_schema,
    prepare_schema_plan,
)


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClient:
    def query(self, sql, settings=None):
        if "FROM system.tables" in sql:
            return Result([])
        if "CN_ENTITY_TRADEMARK_PORTFOLIO" in sql:
            return Result([])
        if "CN_RELATIONSHIP_TIMELINE" in sql:
            return Result([("CN_RELATIONSHIP_TIMELINE", RELATIONSHIP_TIMELINE_READY_VERSION)])
        raise AssertionError(sql)


class TrapClient:
    def __getattr__(self, name):
        raise AssertionError(f"client must not be touched: {name}")


def test_prepare_freezes_clean_missing_schema(tmp_path: Path) -> None:
    sql_path = tmp_path / SQL_RELATIVE_PATH
    sql_path.parent.mkdir(parents=True)
    sql_path.write_text(
        "CREATE TABLE IF NOT EXISTS a(x UInt8);"
        "CREATE TABLE IF NOT EXISTS b(x UInt8);"
        "CREATE MATERIALIZED VIEW IF NOT EXISTS c AS SELECT 1;"
        "INSERT INTO d VALUES (1);",
        encoding="utf-8",
    )
    output = tmp_path / "plan.json"
    envelope = prepare_schema_plan(
        output,
        client=FakeClient(),
        main_sha_getter=lambda: "1" * 40,
        repo_root=tmp_path,
    )
    assert envelope["plan"]["version"] == PLAN_VERSION
    assert envelope["plan"]["pre_schema"] == {}
    assert envelope["plan"]["pre_marker"] == []
    assert envelope["plan"]["mutation_scope"]["destructive_operations"] is False
    assert output.exists()


def test_split_sql_rejects_destructive_statement() -> None:
    with pytest.raises(RuntimeError, match="destructive"):
        _split_sql(
            "CREATE TABLE a(x UInt8);"
            "CREATE TABLE b(x UInt8);"
            "CREATE MATERIALIZED VIEW c AS SELECT 1;"
            "DROP TABLE d;"
        )


def test_apply_requires_exact_authority_before_client_access(tmp_path: Path) -> None:
    plan = {"version": PLAN_VERSION}
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(PermissionError, match="authority"):
        apply_schema(
            path,
            plan_sha=envelope["plan_sha256"],
            authority_token="NO",
            receipt_path=tmp_path / "receipt.json",
            client=TrapClient(),
        )
