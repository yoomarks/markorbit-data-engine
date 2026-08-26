from __future__ import annotations

from pathlib import Path

import pytest

from app.cn.migrations import (
    EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS,
    assert_exact_goods_current_schema,
)


def _goods_rows(columns: tuple[str, ...]) -> list[tuple[str, str, int]]:
    return [
        ("cn_goods_item_current", name, position)
        for position, name in enumerate(columns, start=1)
    ]


def test_exact_goods_current_schema_passes() -> None:
    rows = _goods_rows(EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS)
    rows.extend(
        [
            ("cn_case_current", "case_id", 1),
            ("cn_case_current", "source_rank", 2),
        ]
    )

    assert_exact_goods_current_schema(rows)


def test_legacy_34_column_goods_schema_fails_before_replay() -> None:
    legacy_columns = EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS + (
        "superseded_by_package_id",
        "superseded_at",
        "supersession_reason",
        "supersession_rank",
    )

    with pytest.raises(RuntimeError) as exc_info:
        assert_exact_goods_current_schema(_goods_rows(legacy_columns))

    message = str(exc_info.value)
    assert "cn_goods_item_current" in message
    assert "exactly 30 columns" in message
    assert "found 34" in message
    assert "schema migration required before replay/import" in message
    assert "no CN replay/import was started" in message


def test_misordered_goods_schema_fails_before_replay() -> None:
    columns = list(EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS)
    columns[1], columns[2] = columns[2], columns[1]

    with pytest.raises(RuntimeError) as exc_info:
        assert_exact_goods_current_schema(_goods_rows(tuple(columns)))

    message = str(exc_info.value)
    assert "exactly 30 columns" in message
    assert "found 30" in message
    assert "positional-write order" in message
    assert "Fix the ClickHouse schema before continuing" in message


def test_missing_goods_table_fails_with_migration_message() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        assert_exact_goods_current_schema(
            [("cn_case_current", "source_rank", 1)]
        )

    message = str(exc_info.value)
    assert "cn_goods_item_current" in message
    assert "found 0" in message
    assert "schema migration required before replay/import" in message


def test_goods_schema_gate_matches_clickhouse_ddl_order() -> None:
    ddl = Path("database/clickhouse/init/003_m16_goods_lifecycle.sql").read_text(
        encoding="utf-8"
    )
    marker = "CREATE TABLE IF NOT EXISTS markorbit_facts.cn_goods_item_current"
    start = ddl.index(marker)
    body_start = ddl.index("(\n", start) + 2
    body_end = ddl.index(")\nENGINE", body_start)
    definition_lines = [
        line.strip()
        for line in ddl[body_start:body_end].splitlines()
        if line.strip()
    ]
    ddl_columns = tuple(line.split()[0] for line in definition_lines)

    assert ddl_columns == EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS


def test_ingest_checks_schema_before_package_or_job_work() -> None:
    source = Path("app/cn/ingest.py").read_text(encoding="utf-8")
    start = source.index("def ingest_cn_package(")
    ingest_source = source[start:]

    assert ingest_source.index("ensure_m15_schema()") < ingest_source.index(
        "get_package(str(package_uuid))"
    )
    assert ingest_source.index("ensure_m15_schema()") < ingest_source.index(
        "create_job_run("
    )
