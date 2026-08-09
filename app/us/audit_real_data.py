from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.scanner import sha256_file
from app.us.migrations import US_SCHEMA_VERSION
from app.us.package_meta import DAILY_RANK_MAJOR


AUDIT_VERSION = "US_M13_REAL_DATA_ACCEPTANCE_V1"

CURRENT_TABLE_KEYS = {
    "us_case_current": "case_id",
    "us_owner_current": "owner_key",
    "us_classification_current": "classification_key",
    "us_statement_current": "statement_key",
    "us_correspondent_current": "correspondent_key",
    "us_design_search_current": "design_search_key",
    "us_prior_registration_current": "prior_registration_key",
    "us_foreign_application_current": "foreign_application_key",
    "us_madrid_filing_current": "madrid_filing_key",
}
HISTORY_TABLE_KEYS = {
    "us_event_history": "event_key",
    "us_madrid_event_history": "madrid_event_key",
}
ALL_TABLE_KEYS = {**CURRENT_TABLE_KEYS, **HISTORY_TABLE_KEYS}

PACKAGE_ID_COLUMNS = {
    **{table: "last_source_package_id" for table in CURRENT_TABLE_KEYS},
    **{table: "source_package_id" for table in HISTORY_TABLE_KEYS},
}

CHILD_TABLES = tuple(table for table in ALL_TABLE_KEYS if table != "us_case_current")
M13_TABLES = (
    "us_correspondent_current",
    "us_design_search_current",
    "us_prior_registration_current",
    "us_foreign_application_current",
    "us_madrid_filing_current",
    "us_madrid_event_history",
)


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _int(value: Any) -> int:
    return int(value or 0)


def _package_rows() -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, package_sequence, file_name, file_path, file_size,
                       sha256, package_kind, partition_dimension, partition_value,
                       source_period_start, source_period_end, source_sequence,
                       source_rank, status, profile, schema_version, archived_path,
                       processed_at, error_message
                FROM control.source_package
                WHERE jurisdiction = 'US'
                ORDER BY source_rank, package_sequence
                """
            )
            return [dict(row) for row in cur.fetchall()]


def _postgres_schema_version() -> str:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM control.schema_version WHERE component = 'US_CORE'"
            )
            row = cur.fetchone()
            return str(row["version"]) if row else ""


def _clickhouse_schema_versions() -> list[str]:
    result = clickhouse_client().query(
        """
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'US_CORE'
        ORDER BY version
        """
    )
    return [str(row[0]) for row in result.result_rows]


def _table_metrics() -> dict[str, dict[str, int]]:
    client = clickhouse_client()
    metrics: dict[str, dict[str, int]] = {}
    for table, key_column in ALL_TABLE_KEYS.items():
        active_filter = "WHERE is_deleted = 0" if table in CURRENT_TABLE_KEYS else ""
        row = client.query(
            f"""
            SELECT count() AS row_count,
                   uniqExact({key_column}) AS unique_keys,
                   uniqExact(serial_number) AS serial_count
            FROM markorbit_facts.{table} FINAL
            {active_filter}
            """
        ).result_rows[0]
        duplicate_keys = client.query(
            f"""
            SELECT count()
            FROM
            (
                SELECT {key_column}
                FROM markorbit_facts.{table} FINAL
                {active_filter}
                GROUP BY {key_column}
                HAVING count() > 1
            )
            """
        ).result_rows[0][0]
        metrics[table] = {
            "row_count": _int(row[0]),
            "unique_keys": _int(row[1]),
            "serial_count": _int(row[2]),
            "duplicate_keys_after_final": _int(duplicate_keys),
        }
    return metrics


def _orphan_counts() -> dict[str, int]:
    client = clickhouse_client()
    result: dict[str, int] = {}
    for table in CHILD_TABLES:
        active_filter = "AND is_deleted = 0" if table in CURRENT_TABLE_KEYS else ""
        rows = client.query(
            f"""
            SELECT count()
            FROM
            (
                SELECT DISTINCT serial_number
                FROM markorbit_facts.{table} FINAL
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
        result[table] = _int(rows[0][0] if rows else 0)
    return result


def _lineage_metrics() -> dict[str, list[dict[str, Any]]]:
    client = clickhouse_client()
    result: dict[str, list[dict[str, Any]]] = {}
    for table, package_column in PACKAGE_ID_COLUMNS.items():
        active_filter = "WHERE is_deleted = 0" if table in CURRENT_TABLE_KEYS else ""
        rows = client.query(
            f"""
            SELECT toString({package_column}) AS package_id,
                   min(source_rank) AS min_source_rank,
                   max(source_rank) AS max_source_rank,
                   count() AS row_count
            FROM markorbit_facts.{table} FINAL
            {active_filter}
            GROUP BY {package_column}
            ORDER BY package_id
            """
        ).result_rows
        result[table] = [
            {
                "package_id": str(row[0]),
                "min_source_rank": _int(row[1]),
                "max_source_rank": _int(row[2]),
                "row_count": _int(row[3]),
            }
            for row in rows
        ]
    return result


def _source_kind_case_counts() -> dict[str, int]:
    rows = clickhouse_client().query(
        """
        SELECT source_package_kind, count()
        FROM markorbit_facts.us_case_current FINAL
        WHERE is_deleted = 0
        GROUP BY source_package_kind
        ORDER BY source_package_kind
        """
    ).result_rows
    return {str(kind): _int(count) for kind, count in rows}


def _profile_schema_version(package: dict[str, Any]) -> str:
    profile = package.get("profile") or {}
    if not isinstance(profile, dict):
        return ""
    totals = profile.get("totals") or {}
    if not isinstance(totals, dict):
        return ""
    return str(totals.get("schema_version") or "")


def _tombstone_summary(packages: list[dict[str, Any]]) -> dict[str, Any]:
    by_table: Counter[str] = Counter()
    by_package: list[dict[str, Any]] = []
    total_rows = 0
    total_tombstones = 0
    for package in packages:
        if package.get("status") != "SUCCESS":
            continue
        profile = package.get("profile") or {}
        totals = profile.get("totals") if isinstance(profile, dict) else {}
        totals = totals if isinstance(totals, dict) else {}
        tombstones = totals.get("snapshot_tombstone_counts") or {}
        row_counts = totals.get("row_counts") or {}
        if not isinstance(tombstones, dict):
            tombstones = {}
        if not isinstance(row_counts, dict):
            row_counts = {}
        package_tombstones = sum(_int(value) for value in tombstones.values())
        package_rows = sum(_int(value) for value in row_counts.values())
        total_tombstones += package_tombstones
        total_rows += package_rows
        for table, value in tombstones.items():
            by_table[str(table)] += _int(value)
        by_package.append(
            {
                "package_id": str(package["package_id"]),
                "file_name": package["file_name"],
                "package_kind": package["package_kind"],
                "source_rank": _int(package["source_rank"]),
                "published_rows": package_rows,
                "tombstones": package_tombstones,
                "tombstone_rate": (
                    round(package_tombstones / package_rows, 8) if package_rows else 0.0
                ),
            }
        )
    return {
        "total_published_rows": total_rows,
        "total_tombstones": total_tombstones,
        "overall_tombstone_rate": (
            round(total_tombstones / total_rows, 8) if total_rows else 0.0
        ),
        "by_table": dict(sorted(by_table.items())),
        "by_package": by_package,
    }


def _resolve_source_path(package: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    archived_path = package.get("archived_path")
    file_path = package.get("file_path")
    if archived_path:
        candidates.append(Path(str(archived_path)))
    if file_path:
        candidates.append(Path(str(file_path)))

    raw_root = get_settings().raw_data_root
    file_name = str(package.get("file_name") or "")
    if file_name:
        candidates.extend(
            [
                raw_root / "archive" / "us" / file_name,
                raw_root / "incoming" / "us" / file_name,
            ]
        )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _verify_source_files(packages: list[dict[str, Any]]) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    for package in packages:
        if package.get("status") != "SUCCESS":
            continue
        path = _resolve_source_path(package)
        base = {
            "package_id": str(package["package_id"]),
            "file_name": str(package["file_name"]),
            "expected_sha256": str(package.get("sha256") or "").lower(),
        }
        if path is None:
            missing.append(base)
            continue
        actual = sha256_file(path).lower()
        row = {**base, "path": str(path), "actual_sha256": actual}
        checked.append(row)
        if actual != base["expected_sha256"]:
            mismatched.append(row)
    return {
        "requested": True,
        "checked_count": len(checked),
        "missing_count": len(missing),
        "mismatch_count": len(mismatched),
        "missing": missing,
        "mismatched": mismatched,
    }


def _partition_duplicates(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for package in packages:
        key = (str(package.get("package_kind") or ""), str(package.get("partition_value") or ""))
        grouped[key].append(package)
    duplicates: list[dict[str, Any]] = []
    for (kind, partition), rows in sorted(grouped.items()):
        shas = {str(row.get("sha256") or "") for row in rows}
        if len(rows) > 1 and len(shas) > 1:
            duplicates.append(
                {
                    "package_kind": kind,
                    "partition_value": partition,
                    "sources": [
                        {
                            "package_id": str(row["package_id"]),
                            "file_name": row["file_name"],
                            "sha256": row["sha256"],
                            "status": row["status"],
                        }
                        for row in rows
                    ],
                }
            )
    return duplicates


def evaluate_acceptance(
    *,
    packages: list[dict[str, Any]],
    postgres_schema_version: str,
    clickhouse_schema_versions: list[str],
    table_metrics: dict[str, dict[str, int]],
    orphan_counts: dict[str, int],
    lineage_metrics: dict[str, list[dict[str, Any]]],
    source_kind_case_counts: dict[str, int],
    source_file_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    successful = [row for row in packages if row.get("status") == "SUCCESS"]
    history = [row for row in successful if row.get("package_kind") == "HISTORICAL_APPLICATIONS"]
    daily = [row for row in successful if row.get("package_kind") == "DAILY_APPLICATIONS"]

    status_counts = dict(sorted(Counter(str(row.get("status") or "") for row in packages).items()))
    pending = [
        row
        for row in packages
        if row.get("status") in {"REGISTERED", "PROCESSING", "INTERRUPTED"}
    ]
    failed = [
        row for row in packages if row.get("status") in {"FAILED", "MISSING_FILE"}
    ]

    history_ranks = [_int(row.get("source_rank")) for row in history]
    daily_ranks = [_int(row.get("source_rank")) for row in daily]
    rank_boundary_ok = not history_ranks or not daily_ranks or max(history_ranks) < min(daily_ranks)
    daily_major_ok = all(rank >= DAILY_RANK_MAJOR for rank in daily_ranks)
    history_major_ok = all(rank < DAILY_RANK_MAJOR for rank in history_ranks)

    package_lookup = {str(row["package_id"]): row for row in packages}
    unknown_lineage: list[dict[str, Any]] = []
    rank_mismatches: list[dict[str, Any]] = []
    for table, rows in lineage_metrics.items():
        for row in rows:
            package = package_lookup.get(str(row["package_id"]))
            if package is None:
                unknown_lineage.append({"table": table, **row})
                continue
            expected_rank = _int(package.get("source_rank"))
            if row["min_source_rank"] != expected_rank or row["max_source_rank"] != expected_rank:
                rank_mismatches.append(
                    {
                        "table": table,
                        **row,
                        "registered_source_rank": expected_rank,
                    }
                )

    duplicate_tables = {
        table: metrics["duplicate_keys_after_final"]
        for table, metrics in table_metrics.items()
        if metrics.get("duplicate_keys_after_final")
    }
    orphan_tables = {table: count for table, count in orphan_counts.items() if count}
    empty_tables = [
        table for table, metrics in table_metrics.items() if metrics.get("row_count", 0) == 0
    ]
    empty_m13_tables = [table for table in M13_TABLES if table in empty_tables]

    replay_version_mismatches = [
        {
            "package_id": str(row["package_id"]),
            "file_name": row["file_name"],
            "package_kind": row["package_kind"],
            "profile_schema_version": _profile_schema_version(row),
        }
        for row in successful
        if _profile_schema_version(row) != US_SCHEMA_VERSION
    ]
    partition_duplicates = _partition_duplicates(packages)

    hard_fail_reasons: list[str] = []
    if failed:
        hard_fail_reasons.append("failed_or_missing_source_packages")
    if partition_duplicates:
        hard_fail_reasons.append("ambiguous_semantic_partition_sources")
    if not rank_boundary_ok or not daily_major_ok or not history_major_ok:
        hard_fail_reasons.append("source_rank_precedence_violation")
    if duplicate_tables:
        hard_fail_reasons.append("duplicates_after_final")
    if orphan_tables:
        hard_fail_reasons.append("subordinate_rows_without_case")
    if unknown_lineage:
        hard_fail_reasons.append("unregistered_source_lineage")
    if rank_mismatches:
        hard_fail_reasons.append("source_lineage_rank_mismatch")
    if source_file_verification:
        if source_file_verification.get("missing_count"):
            hard_fail_reasons.append("authoritative_source_files_missing")
        if source_file_verification.get("mismatch_count"):
            hard_fail_reasons.append("authoritative_source_sha_mismatch")
    if history and daily and empty_m13_tables:
        hard_fail_reasons.append("m13_fact_tables_empty_after_history_daily_replay")
    if daily and source_kind_case_counts.get("DAILY_APPLICATIONS", 0) == 0:
        hard_fail_reasons.append("daily_packages_loaded_but_no_daily_current_cases")

    not_ready_reasons: list[str] = []
    if not packages:
        not_ready_reasons.append("no_us_packages_registered")
    if not history:
        not_ready_reasons.append("historical_baseline_not_successful")
    if not daily:
        not_ready_reasons.append("daily_update_not_successful")
    if pending:
        not_ready_reasons.append("registered_replay_not_complete")
    if replay_version_mismatches:
        not_ready_reasons.append("successful_packages_require_m13_replay")
    if postgres_schema_version != US_SCHEMA_VERSION:
        not_ready_reasons.append("postgres_us_schema_version_not_m13")
    if US_SCHEMA_VERSION not in clickhouse_schema_versions:
        not_ready_reasons.append("clickhouse_us_schema_version_not_m13")

    warning_reasons: list[str] = []
    if source_file_verification is None:
        warning_reasons.append("source_sha_verification_not_requested")

    if hard_fail_reasons:
        status = "FAIL"
    elif not_ready_reasons:
        status = "NOT_READY"
    elif warning_reasons:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    history_starts = [_as_date(row.get("source_period_start")) for row in history]
    history_ends = [_as_date(row.get("source_period_end")) for row in history]
    daily_dates = [_as_date(row.get("source_period_end")) for row in daily]
    history_starts = [value for value in history_starts if value]
    history_ends = [value for value in history_ends if value]
    daily_dates = [value for value in daily_dates if value]

    return {
        "status": status,
        "audit": "US_M13_REAL_DATA_ACCEPTANCE",
        "audit_version": AUDIT_VERSION,
        "us_model_version": US_SCHEMA_VERSION,
        "hard_fail_reasons": hard_fail_reasons,
        "not_ready_reasons": not_ready_reasons,
        "warning_reasons": warning_reasons,
        "schema": {
            "postgres_us_core": postgres_schema_version,
            "clickhouse_us_core_versions": clickhouse_schema_versions,
        },
        "packages": {
            "registered_count": len(packages),
            "success_count": len(successful),
            "history_success_count": len(history),
            "daily_success_count": len(daily),
            "status_counts": status_counts,
            "pending": [
                {
                    "package_id": str(row["package_id"]),
                    "file_name": row["file_name"],
                    "status": row["status"],
                }
                for row in pending
            ],
            "failed_or_missing": [
                {
                    "package_id": str(row["package_id"]),
                    "file_name": row["file_name"],
                    "status": row["status"],
                    "error_message": row.get("error_message"),
                }
                for row in failed
            ],
            "replay_version_mismatches": replay_version_mismatches,
            "ambiguous_partitions": partition_duplicates,
        },
        "coverage": {
            "historical_start": min(history_starts).isoformat() if history_starts else None,
            "historical_end": max(history_ends).isoformat() if history_ends else None,
            "daily_start": min(daily_dates).isoformat() if daily_dates else None,
            "daily_end": max(daily_dates).isoformat() if daily_dates else None,
            "history_max_source_rank": max(history_ranks) if history_ranks else None,
            "daily_min_source_rank": min(daily_ranks) if daily_ranks else None,
            "rank_boundary_ok": rank_boundary_ok and daily_major_ok and history_major_ok,
            "current_case_source_kind_counts": source_kind_case_counts,
        },
        "tables": table_metrics,
        "integrity": {
            "duplicates_after_final": duplicate_tables,
            "orphan_serials_by_table": orphan_tables,
            "unregistered_source_lineage": unknown_lineage,
            "source_lineage_rank_mismatches": rank_mismatches,
            "empty_tables": empty_tables,
        },
        "snapshot_reconciliation": _tombstone_summary(packages),
        "source_file_verification": source_file_verification
        or {"requested": False, "checked_count": 0},
        "acceptance_note": (
            "PASS means the registered historical baseline and at least one daily update are fully "
            "replayed under US_M1.3 with intact precedence, FINAL uniqueness, case lineage, and no "
            "failed registered sources. PASS_WITH_WARNINGS differs only because full source SHA "
            "verification was not requested. NOT_READY means replay/evidence is incomplete, not that "
            "the durable data is corrupt."
        ),
    }


def build_audit(*, verify_source_files: bool = False) -> dict[str, Any]:
    packages = _package_rows()
    verification = _verify_source_files(packages) if verify_source_files else None
    return evaluate_acceptance(
        packages=packages,
        postgres_schema_version=_postgres_schema_version(),
        clickhouse_schema_versions=_clickhouse_schema_versions(),
        table_metrics=_table_metrics(),
        orphan_counts=_orphan_counts(),
        lineage_metrics=_lineage_metrics(),
        source_kind_case_counts=_source_kind_case_counts(),
        source_file_verification=verification,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only US M1.3 real-data acceptance audit")
    parser.add_argument(
        "--verify-source-files",
        action="store_true",
        help="Re-hash every successful authoritative US source file and compare with registry SHA-256.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build_audit(verify_source_files=args.verify_source_files),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
