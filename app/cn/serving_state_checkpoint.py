from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from app.cn.migrations import assert_exact_goods_current_schema
from app.db import clickhouse_client, postgres_conn


CHECKPOINT_VERSION = "CN_M16_LIGHTWEIGHT_SERVING_CHECKPOINT_V1"
DEFAULT_EXPECTED_FILE_NAME = "2023_5.zip"
DISK_WARN_FREE_RATIO = 0.20
DISK_BLOCK_FREE_RATIO = 0.10

CRITICAL_TABLES = (
    "cn_case_current",
    "cn_case_scope_current",
    "cn_case_party_current",
    "cn_goods_item_current",
    "cn_goods_scope_lifecycle_current",
    "cn_observed_event",
)

_TABLE_LIST_SQL = ", ".join(f"'{table}'" for table in CRITICAL_TABLES)

POSTGRES_EXPECTED_PACKAGE_SQL = """
    SELECT file_name, status, processed_at, error_message, package_sequence
    FROM control.source_package
    WHERE jurisdiction = 'CN' AND file_name = %s
    ORDER BY package_sequence DESC
    LIMIT 1
"""

POSTGRES_PROCESSING_COUNT_SQL = """
    SELECT count(*) AS processing_count
    FROM control.source_package
    WHERE jurisdiction = 'CN' AND status = 'PROCESSING'
"""

CLICKHOUSE_TABLES_SQL = f"""
    SELECT name
    FROM system.tables
    WHERE database = 'markorbit_facts'
      AND name IN ({_TABLE_LIST_SQL})
    ORDER BY name
"""


def _parts_clause(table: str) -> str:
    return f"""
    SELECT
        '{table}' AS table_name,
        count() AS active_parts,
        coalesce(sum(bytes_on_disk), 0) AS bytes_on_disk,
        coalesce(sum(rows), 0) AS rows_from_parts
    FROM system.parts
    WHERE database = 'markorbit_facts'
      AND active
      AND table = '{table}'
    """.strip()


CLICKHOUSE_ACTIVE_PARTS_SQL = "\nUNION ALL\n".join(
    _parts_clause(table) for table in CRITICAL_TABLES
)

CLICKHOUSE_GOODS_COLUMNS_SQL = """
    SELECT table, name, position
    FROM system.columns
    WHERE database = 'markorbit_facts'
      AND table = 'cn_goods_item_current'
    ORDER BY position
"""

CLICKHOUSE_DISKS_SQL = """
    SELECT name, path, free_space, total_space, keep_free_space
    FROM system.disks
    ORDER BY name
"""

READ_ONLY_QUERIES = (
    POSTGRES_EXPECTED_PACKAGE_SQL,
    POSTGRES_PROCESSING_COUNT_SQL,
    CLICKHOUSE_TABLES_SQL,
    CLICKHOUSE_ACTIVE_PARTS_SQL,
    CLICKHOUSE_GOODS_COLUMNS_SQL,
    CLICKHOUSE_DISKS_SQL,
)


def _reason(code: str, message: str, severity: str) -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _parts_report(
    rows: list[tuple[Any, Any, Any, Any]],
) -> dict[str, dict[str, int]]:
    result = {
        table: {"active_parts": 0, "bytes_on_disk": 0, "rows_from_parts": 0}
        for table in CRITICAL_TABLES
    }
    for table, active_parts, bytes_on_disk, rows_count in rows:
        table_name = str(table)
        if table_name not in result:
            continue
        result[table_name] = {
            "active_parts": int(active_parts or 0),
            "bytes_on_disk": int(bytes_on_disk or 0),
            "rows_from_parts": int(rows_count or 0),
        }
    return result


def _disk_report(rows: list[tuple[Any, Any, Any, Any, Any]]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for name, path, free_space, total_space, keep_free_space in rows:
        free_bytes = int(free_space or 0)
        total_bytes = int(total_space or 0)
        report.append(
            {
                "name": str(name),
                "path": str(path),
                "free_space": free_bytes,
                "total_space": total_bytes,
                "keep_free_space": int(keep_free_space or 0),
                "free_ratio": (
                    round(free_bytes / total_bytes, 6) if total_bytes > 0 else None
                ),
            }
        )
    return report


def evaluate_serving_state(
    *,
    expected_file_name: str,
    expected_package: dict[str, Any] | None,
    processing_package_count: int,
    existing_tables: set[str],
    parts: dict[str, dict[str, int]],
    goods_schema_error: str | None,
    disks: list[dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []

    expected_package_success = bool(
        expected_package is not None
        and str(expected_package.get("status") or "") == "SUCCESS"
    )
    if expected_package is None:
        reasons.append(
            _reason(
                "EXPECTED_PACKAGE_MISSING",
                f"CN package {expected_file_name!r} is not registered.",
                "BLOCKED",
            )
        )
    elif not expected_package_success:
        reasons.append(
            _reason(
                "EXPECTED_PACKAGE_NOT_SUCCESS",
                f"CN package {expected_file_name!r} status is "
                f"{expected_package.get('status')!r}, not 'SUCCESS'.",
                "BLOCKED",
            )
        )

    quiescent = processing_package_count == 0
    if not quiescent:
        reasons.append(
            _reason(
                "CN_PACKAGE_PROCESSING",
                f"{processing_package_count} CN package(s) are currently PROCESSING.",
                "BLOCKED",
            )
        )

    tables_ready = True
    for table in CRITICAL_TABLES:
        if table not in existing_tables:
            tables_ready = False
            reasons.append(
                _reason(
                    "CRITICAL_TABLE_MISSING",
                    f"markorbit_facts.{table} is missing.",
                    "BLOCKED",
                )
            )
            continue
        if int(parts.get(table, {}).get("active_parts", 0)) <= 0:
            tables_ready = False
            reasons.append(
                _reason(
                    "CRITICAL_TABLE_NO_ACTIVE_PARTS",
                    f"markorbit_facts.{table} has no active parts.",
                    "BLOCKED",
                )
            )

    goods_schema_exact = goods_schema_error is None
    if not goods_schema_exact:
        reasons.append(
            _reason("GOODS_SCHEMA_MISMATCH", goods_schema_error or "", "BLOCKED")
        )

    usable_disks = [disk for disk in disks if disk.get("free_ratio") is not None]
    if not usable_disks:
        reasons.append(
            _reason(
                "CLICKHOUSE_DISK_METADATA_MISSING",
                "ClickHouse did not report usable disk capacity metadata.",
                "BLOCKED",
            )
        )
    else:
        for disk in usable_disks:
            free_ratio = float(disk["free_ratio"])
            if free_ratio < DISK_BLOCK_FREE_RATIO:
                reasons.append(
                    _reason(
                        "CLICKHOUSE_DISK_CRITICAL",
                        f"ClickHouse disk {disk['name']!r} has only "
                        f"{free_ratio:.1%} free space.",
                        "BLOCKED",
                    )
                )
            elif free_ratio < DISK_WARN_FREE_RATIO:
                reasons.append(
                    _reason(
                        "CLICKHOUSE_DISK_LOW_FREE",
                        f"ClickHouse disk {disk['name']!r} has "
                        f"{free_ratio:.1%} free space.",
                        "WARN",
                    )
                )

    severities = {reason["severity"] for reason in reasons}
    if "BLOCKED" in severities:
        status = "BLOCKED"
    elif "WARN" in severities:
        status = "WARN"
    else:
        status = "PASS"

    core_tables_ready = tables_ready and goods_schema_exact
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "status": status,
        "read_only": True,
        "evidence_mode": "LIGHTWEIGHT_SERVING_CHECKPOINT",
        "expected_file_name": expected_file_name,
        "expected_package": expected_package,
        "expected_package_success": expected_package_success,
        "processing_package_count": processing_package_count,
        "quiescent": quiescent,
        "critical_tables": {
            table: {
                "exists": table in existing_tables,
                **parts.get(
                    table,
                    {"active_parts": 0, "bytes_on_disk": 0, "rows_from_parts": 0},
                ),
            }
            for table in CRITICAL_TABLES
        },
        "core_tables_ready": core_tables_ready,
        "goods_schema_exact": goods_schema_exact,
        "disks": disks,
        "reasons": reasons,
        "query_scope": "control_and_system_metadata_only",
        "full_corpus_scan": False,
        "package_reprocessed": False,
        "full_corpus_semantic_acceptance_claimed": False,
    }


def build_serving_state_checkpoint(
    expected_file_name: str = DEFAULT_EXPECTED_FILE_NAME,
    *,
    postgres_connection_factory: Callable[[], AbstractContextManager[Any]] = postgres_conn,
    clickhouse_client_factory: Callable[[], Any] = clickhouse_client,
) -> dict[str, Any]:
    with postgres_connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(POSTGRES_EXPECTED_PACKAGE_SQL, (expected_file_name,))
            package_row = cur.fetchone()
            cur.execute(POSTGRES_PROCESSING_COUNT_SQL)
            processing_row = cur.fetchone()

    expected_package = dict(package_row) if package_row else None
    processing_package_count = int(
        (processing_row or {}).get("processing_count", 0) or 0
    )

    client = clickhouse_client_factory()
    existing_tables = {
        str(row[0]) for row in client.query(CLICKHOUSE_TABLES_SQL).result_rows
    }
    parts = _parts_report(client.query(CLICKHOUSE_ACTIVE_PARTS_SQL).result_rows)
    goods_columns = client.query(CLICKHOUSE_GOODS_COLUMNS_SQL).result_rows
    goods_schema_error: str | None = None
    try:
        assert_exact_goods_current_schema(goods_columns)
    except RuntimeError as exc:
        goods_schema_error = str(exc)
    disks = _disk_report(client.query(CLICKHOUSE_DISKS_SQL).result_rows)

    return evaluate_serving_state(
        expected_file_name=expected_file_name,
        expected_package=expected_package,
        processing_package_count=processing_package_count,
        existing_tables=existing_tables,
        parts=parts,
        goods_schema_error=goods_schema_error,
        disks=disks,
    )


def _execution_error_report(expected_file_name: str, exc: Exception) -> dict[str, Any]:
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "status": "BLOCKED",
        "read_only": True,
        "evidence_mode": "LIGHTWEIGHT_SERVING_CHECKPOINT",
        "expected_file_name": expected_file_name,
        "expected_package_success": False,
        "processing_package_count": None,
        "quiescent": False,
        "core_tables_ready": False,
        "goods_schema_exact": False,
        "reasons": [
            _reason(
                "CHECKPOINT_EXECUTION_ERROR",
                f"Lightweight serving-state checkpoint could not complete: {exc}",
                "BLOCKED",
            )
        ],
        "query_scope": "control_and_system_metadata_only",
        "full_corpus_scan": False,
        "package_reprocessed": False,
        "full_corpus_semantic_acceptance_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only, metadata-only CN serving-state checkpoint."
    )
    parser.add_argument(
        "--expected-file-name",
        default=DEFAULT_EXPECTED_FILE_NAME,
        help="Expected successful CN source package file name.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        report = build_serving_state_checkpoint(args.expected_file_name)
    except Exception as exc:
        report = _execution_error_report(args.expected_file_name, exc)

    if args.compact:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] in {"PASS", "WARN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
