from __future__ import annotations

import sys
from typing import Any

from app.us import audit_real_data_core as _core


DURABLE_HISTORY_TABLE = "us_case_observation_history"
_core.AUDIT_VERSION = "US_M14_REAL_DATA_ACCEPTANCE_V1"
_core.HISTORY_TABLE_KEYS[DURABLE_HISTORY_TABLE] = "observation_key"
_core.ALL_TABLE_KEYS = {**_core.CURRENT_TABLE_KEYS, **_core.HISTORY_TABLE_KEYS}
_core.PACKAGE_ID_COLUMNS = {
    **{table: "last_source_package_id" for table in _core.CURRENT_TABLE_KEYS},
    **{table: "source_package_id" for table in _core.HISTORY_TABLE_KEYS},
}
_core.CHILD_TABLES = tuple(
    table for table in _core.ALL_TABLE_KEYS if table != "us_case_current"
)


def _table_suffix(table: str) -> str:
    return "" if table == DURABLE_HISTORY_TABLE else " FINAL"


def _m14_table_metrics() -> dict[str, dict[str, int]]:
    client = _core.clickhouse_client()
    metrics: dict[str, dict[str, int]] = {}
    for table, key_column in _core.ALL_TABLE_KEYS.items():
        active_filter = "WHERE is_deleted = 0" if table in _core.CURRENT_TABLE_KEYS else ""
        suffix = _table_suffix(table)
        row = client.query(
            f"""
            SELECT count() AS row_count,
                   uniqExact({key_column}) AS unique_keys,
                   uniqExact(serial_number) AS serial_count
            FROM markorbit_facts.{table}{suffix}
            {active_filter}
            """
        ).result_rows[0]
        duplicate_keys = client.query(
            f"""
            SELECT count()
            FROM
            (
                SELECT {key_column}
                FROM markorbit_facts.{table}{suffix}
                {active_filter}
                GROUP BY {key_column}
                HAVING count() > 1
            )
            """
        ).result_rows[0][0]
        metrics[table] = {
            "row_count": _core._int(row[0]),
            "unique_keys": _core._int(row[1]),
            "serial_count": _core._int(row[2]),
            "duplicate_keys_after_final": _core._int(duplicate_keys),
        }
    return metrics


def _m14_orphan_counts() -> dict[str, int]:
    client = _core.clickhouse_client()
    result: dict[str, int] = {}
    for table in _core.CHILD_TABLES:
        active_filter = "AND is_deleted = 0" if table in _core.CURRENT_TABLE_KEYS else ""
        suffix = _table_suffix(table)
        rows = client.query(
            f"""
            SELECT count()
            FROM
            (
                SELECT DISTINCT serial_number
                FROM markorbit_facts.{table}{suffix}
                WHERE serial_number != '' {active_filter}
            ) AS child
            WHERE child.serial_number NOT IN
            (
                SELECT serial_number
                FROM markorbit_facts.us_case_current FINAL
                WHERE is_deleted = 0
            )
            """
        ).result_rows
        result[table] = _core._int(rows[0][0] if rows else 0)
    return result


def _m14_lineage_metrics() -> dict[str, list[dict[str, Any]]]:
    client = _core.clickhouse_client()
    result: dict[str, list[dict[str, Any]]] = {}
    for table, package_column in _core.PACKAGE_ID_COLUMNS.items():
        active_filter = "WHERE is_deleted = 0" if table in _core.CURRENT_TABLE_KEYS else ""
        suffix = _table_suffix(table)
        rows = client.query(
            f"""
            SELECT toString({package_column}) AS package_id,
                   min(source_rank) AS min_source_rank,
                   max(source_rank) AS max_source_rank,
                   count() AS row_count
            FROM markorbit_facts.{table}{suffix}
            {active_filter}
            GROUP BY {package_column}
            ORDER BY package_id
            """
        ).result_rows
        result[table] = [
            {
                "package_id": str(row[0]),
                "min_source_rank": _core._int(row[1]),
                "max_source_rank": _core._int(row[2]),
                "row_count": _core._int(row[3]),
            }
            for row in rows
        ]
    return result


_original_evaluate_acceptance = _core.evaluate_acceptance


def _m14_evaluate_acceptance(**kwargs: Any) -> dict[str, Any]:
    report = _original_evaluate_acceptance(**kwargs)
    packages = list(kwargs.get("packages") or [])
    table_metrics = dict(kwargs.get("table_metrics") or {})
    successful = [row for row in packages if row.get("status") == "SUCCESS"]
    has_history = any(
        row.get("package_kind") == "HISTORICAL_APPLICATIONS" for row in successful
    )
    has_daily = any(row.get("package_kind") == "DAILY_APPLICATIONS" for row in successful)
    durable_rows = int(
        (table_metrics.get(DURABLE_HISTORY_TABLE) or {}).get("row_count") or 0
    )

    hard = list(report.get("hard_fail_reasons") or [])
    if has_history and has_daily and durable_rows == 0:
        hard.append("m14_durable_history_empty_after_history_daily_replay")
    report["hard_fail_reasons"] = list(dict.fromkeys(hard))

    reason_upgrades = {
        "successful_packages_require_m13_replay": "successful_packages_require_m14_replay",
        "postgres_us_schema_version_not_m13": "postgres_us_schema_version_not_m14",
        "clickhouse_us_schema_version_not_m13": "clickhouse_us_schema_version_not_m14",
    }
    report["not_ready_reasons"] = [
        reason_upgrades.get(reason, reason)
        for reason in report.get("not_ready_reasons") or []
    ]

    if report["hard_fail_reasons"]:
        report["status"] = "FAIL"
    elif report["not_ready_reasons"]:
        report["status"] = "NOT_READY"
    elif report.get("warning_reasons"):
        report["status"] = "PASS_WITH_WARNINGS"
    else:
        report["status"] = "PASS"

    report["audit"] = "US_M14_REAL_DATA_ACCEPTANCE"
    report["audit_version"] = _core.AUDIT_VERSION
    report["durable_history"] = {
        "table": DURABLE_HISTORY_TABLE,
        "row_count": durable_rows,
        "required_after_history_daily_replay": True,
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
    }
    report["acceptance_note"] = (
        "PASS means the registered historical baseline and at least one daily update are fully "
        "replayed under US_M1.4 with intact source precedence, current/event lineage, and durable "
        "case observations. Durable owner/status diffs remain source observations, not legal status "
        "or legal ownership conclusions. PASS_WITH_WARNINGS differs only because full source SHA "
        "verification was not requested. NOT_READY means replay/evidence is incomplete, not corrupt."
    )
    return report


_core._table_metrics = _m14_table_metrics
_core._orphan_counts = _m14_orphan_counts
_core._lineage_metrics = _m14_lineage_metrics
_core.evaluate_acceptance = _m14_evaluate_acceptance

# Preserve the historical import surface while extending it in place. Callers importing
# app.us.audit_real_data continue to receive the same core module and monkeypatch targets.
if __name__ == "__main__":
    _core.main()
else:
    sys.modules[__name__] = _core
