from __future__ import annotations

from pathlib import Path

from app.cn.migrations import EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS
from app.cn.serving_state_checkpoint import (
    CLICKHOUSE_ACTIVE_PARTS_SQL,
    CLICKHOUSE_DISKS_SQL,
    CLICKHOUSE_GOODS_COLUMNS_SQL,
    CLICKHOUSE_TABLES_SQL,
    CRITICAL_TABLES,
    POSTGRES_EXPECTED_PACKAGE_SQL,
    POSTGRES_PROCESSING_COUNT_SQL,
    READ_ONLY_QUERIES,
    _aggregate_parts,
    _disk_report,
    evaluate_serving_state,
)


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "check-cn-serving-state.ps1"


def _healthy_parts() -> dict[str, dict[str, int]]:
    return {
        table: {"active_parts": 1, "bytes_on_disk": 1024, "rows_from_parts": 10}
        for table in CRITICAL_TABLES
    }


def _healthy_disks() -> list[dict[str, object]]:
    return [
        {
            "name": "default",
            "path": "/var/lib/clickhouse/",
            "free_space": 300,
            "total_space": 1000,
            "keep_free_space": 0,
            "free_ratio": 0.3,
        }
    ]


def _evaluate(**overrides):
    values = {
        "expected_file_name": "2023_5.zip",
        "expected_package": {
            "file_name": "2023_5.zip",
            "status": "SUCCESS",
            "processed_at": "2026-08-01T00:00:00+00:00",
            "error_message": None,
            "package_sequence": 1,
        },
        "processing_package_count": 0,
        "existing_tables": set(CRITICAL_TABLES),
        "parts": _healthy_parts(),
        "goods_schema_error": None,
        "disks": _healthy_disks(),
    }
    values.update(overrides)
    return evaluate_serving_state(**values)


def test_healthy_state_passes_without_corpus_acceptance() -> None:
    report = _evaluate()

    assert report["status"] == "PASS"
    assert report["read_only"] is True
    assert report["goods_schema_exact"] is True
    assert report["processing_package_count"] == 0
    assert report["query_scope"] == "control_and_system_metadata_only"
    assert report["reasons"] == []


def test_low_disk_is_warning_not_blocker() -> None:
    disks = _healthy_disks()
    disks[0]["free_ratio"] = 0.15

    report = _evaluate(disks=disks)

    assert report["status"] == "WARN"
    assert [reason["code"] for reason in report["reasons"]] == [
        "CLICKHOUSE_DISK_LOW_FREE"
    ]


def test_critical_disk_blocks() -> None:
    disks = _healthy_disks()
    disks[0]["free_ratio"] = 0.05

    report = _evaluate(disks=disks)

    assert report["status"] == "BLOCKED"
    assert "CLICKHOUSE_DISK_CRITICAL" in {
        reason["code"] for reason in report["reasons"]
    }


def test_package_processing_or_schema_drift_blocks() -> None:
    report = _evaluate(
        processing_package_count=1,
        goods_schema_error="legacy 34-column goods schema",
    )

    assert report["status"] == "BLOCKED"
    codes = {reason["code"] for reason in report["reasons"]}
    assert "CN_PACKAGE_PROCESSING" in codes
    assert "GOODS_SCHEMA_MISMATCH" in codes


def test_missing_expected_package_blocks() -> None:
    report = _evaluate(expected_package=None)

    assert report["status"] == "BLOCKED"
    assert "EXPECTED_PACKAGE_MISSING" in {
        reason["code"] for reason in report["reasons"]
    }


def test_non_success_expected_package_blocks() -> None:
    report = _evaluate(
        expected_package={
            "file_name": "2023_5.zip",
            "status": "FAILED",
            "processed_at": None,
            "error_message": "fixture",
            "package_sequence": 1,
        }
    )

    assert report["status"] == "BLOCKED"
    assert "EXPECTED_PACKAGE_NOT_SUCCESS" in {
        reason["code"] for reason in report["reasons"]
    }


def test_missing_table_or_active_parts_blocks() -> None:
    existing = set(CRITICAL_TABLES)
    existing.remove("cn_observed_event")
    parts = _healthy_parts()
    parts["cn_case_party_current"]["active_parts"] = 0

    report = _evaluate(existing_tables=existing, parts=parts)

    assert report["status"] == "BLOCKED"
    codes = [reason["code"] for reason in report["reasons"]]
    assert "CRITICAL_TABLE_MISSING" in codes
    assert "CRITICAL_TABLE_NO_ACTIVE_PARTS" in codes


def test_missing_disk_metadata_blocks() -> None:
    report = _evaluate(disks=[])

    assert report["status"] == "BLOCKED"
    assert "CLICKHOUSE_DISK_METADATA_MISSING" in {
        reason["code"] for reason in report["reasons"]
    }


def test_parts_are_aggregated_in_python_not_sql() -> None:
    rows = [
        ("cn_case_current", 100, 10),
        ("cn_case_current", 200, 20),
        ("cn_observed_event", 300, 30),
    ]

    report = _aggregate_parts(rows)

    assert report["cn_case_current"] == {
        "active_parts": 2,
        "bytes_on_disk": 300,
        "rows_from_parts": 30,
    }
    assert report["cn_observed_event"]["active_parts"] == 1


def test_disk_report_computes_free_ratio_without_table_scan() -> None:
    report = _disk_report(
        [("default", "/var/lib/clickhouse/", 250, 1000, 0)]
    )

    assert report[0]["free_ratio"] == 0.25
    assert report[0]["free_space"] == 250
    assert report[0]["total_space"] == 1000


def test_query_contract_is_metadata_control_only() -> None:
    assert POSTGRES_EXPECTED_PACKAGE_SQL in READ_ONLY_QUERIES
    assert POSTGRES_PROCESSING_COUNT_SQL in READ_ONLY_QUERIES
    assert CLICKHOUSE_TABLES_SQL in READ_ONLY_QUERIES
    assert CLICKHOUSE_ACTIVE_PARTS_SQL in READ_ONLY_QUERIES
    assert CLICKHOUSE_GOODS_COLUMNS_SQL in READ_ONLY_QUERIES
    assert CLICKHOUSE_DISKS_SQL in READ_ONLY_QUERIES

    joined = "\n".join(READ_ONLY_QUERIES).upper()
    assert "SYSTEM.TABLES" in joined
    assert "SYSTEM.PARTS" in joined
    assert "SYSTEM.COLUMNS" in joined
    assert "SYSTEM.DISKS" in joined
    assert "CONTROL.SOURCE_PACKAGE" in joined

    forbidden = (
        " FINAL",
        " JOIN ",
        "UNIQEXACT",
        "GROUP BY",
        "OPTIMIZE",
        "ALTER TABLE",
        "DELETE ",
        "INSERT ",
        "UPDATE ",
        "SYSTEM.PART_LOG",
    )
    for marker in forbidden:
        assert marker not in joined


def test_goods_schema_fixture_matches_30_column_contract() -> None:
    rows = [
        ("cn_goods_item_current", name, position)
        for position, name in enumerate(
            EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS,
            start=1,
        )
    ]

    assert len(rows) == 30
    assert rows[0] == ("cn_goods_item_current", "case_id", 1)
    assert rows[-1] == ("cn_goods_item_current", "is_deleted", 30)


def test_operator_wrapper_is_local_read_only_and_never_starts_services() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "app.cn.serving_state_checkpoint" in text
    assert "--expected-file-name" in text
    assert "ConvertFrom-Json" in text
    assert "reports" in text
    assert "reason" in lowered

    forbidden = (
        "docker",
        "compose up",
        "compose run",
        "start-process",
        "restart-service",
        "2023_5.zip" + " ",
        "post_import_acceptance",
        "final_checkpoint",
        "optimize table",
    )
    for marker in forbidden:
        assert marker not in lowered
