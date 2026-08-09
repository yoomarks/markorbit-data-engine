from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.db import clickhouse_client, postgres_conn
from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION
from app.us_assignment.repository import list_assignment_packages


AUDIT_VERSION = "US_ASSIGNMENT_M1.0_REAL_DATA_ACCEPTANCE_V1"
SEMANTICS = "RECORDED_ASSIGNMENT_DATA_ACCEPTANCE_NOT_LEGAL_TITLE_CONCLUSION"
_TABLE_KEYS = {
    "us_assignment_record_history": "observation_key",
    "us_assignment_assignor_history": "observation_key",
    "us_assignment_assignee_history": "observation_key",
    "us_assignment_property_history": "observation_key",
}


def _int(value: object) -> int:
    return int(value or 0)


def schema_state() -> dict[str, Any]:
    client = clickhouse_client()
    tables = {
        str(row[0])
        for row in client.query(
            """
            SELECT name FROM system.tables
            WHERE database = 'markorbit_facts' AND name LIKE 'us_assignment_%'
            """
        ).result_rows
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM control.schema_version WHERE component = 'US_ASSIGNMENT'"
            )
            row = cur.fetchone()
    postgres_version = str(row["version"]) if row else None
    clickhouse_versions = [
        str(row[0])
        for row in client.query(
            """
            SELECT version FROM markorbit_facts.schema_version FINAL
            WHERE component = 'US_ASSIGNMENT'
            ORDER BY version
            """
        ).result_rows
    ]
    missing = sorted(set(_TABLE_KEYS) - tables)
    return {
        "expected": ASSIGNMENT_SCHEMA_VERSION,
        "postgres": postgres_version,
        "clickhouse_versions": clickhouse_versions,
        "missing_tables": missing,
        "ready": (
            postgres_version == ASSIGNMENT_SCHEMA_VERSION
            and ASSIGNMENT_SCHEMA_VERSION in clickhouse_versions
            and not missing
        ),
    }


def table_metrics() -> dict[str, dict[str, int]]:
    client = clickhouse_client()
    result: dict[str, dict[str, int]] = {}
    for table, key in _TABLE_KEYS.items():
        row = client.query(
            f"""
            SELECT count(), uniqExact({key}), uniqExact(reel_frame_id),
                   uniqExact(source_package_id)
            FROM markorbit_facts.{table}
            """
        ).result_rows[0]
        duplicate_keys = client.query(
            f"""
            SELECT count() FROM
            (
                SELECT {key}
                FROM markorbit_facts.{table}
                GROUP BY {key}
                HAVING count() > 1
            )
            """
        ).result_rows[0][0]
        result[table] = {
            "row_count": _int(row[0]),
            "unique_observation_keys": _int(row[1]),
            "reel_frame_count": _int(row[2]),
            "source_package_count": _int(row[3]),
            "duplicate_observation_keys": _int(duplicate_keys),
        }
    return result


def lineage_metrics() -> dict[str, list[dict[str, Any]]]:
    client = clickhouse_client()
    result: dict[str, list[dict[str, Any]]] = {}
    for table in _TABLE_KEYS:
        rows = client.query(
            f"""
            SELECT toString(source_package_id), min(source_rank), max(source_rank), count()
            FROM markorbit_facts.{table}
            GROUP BY source_package_id
            ORDER BY source_package_id
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


def orphan_counts() -> dict[str, int]:
    client = clickhouse_client()
    result: dict[str, int] = {}
    for table in (
        "us_assignment_assignor_history",
        "us_assignment_assignee_history",
        "us_assignment_property_history",
    ):
        rows = client.query(
            f"""
            SELECT count() FROM
            (
                SELECT DISTINCT reel_frame_id, source_package_id
                FROM markorbit_facts.{table}
            ) AS child
            LEFT JOIN
            (
                SELECT DISTINCT reel_frame_id, source_package_id
                FROM markorbit_facts.us_assignment_record_history
            ) AS parent
            USING (reel_frame_id, source_package_id)
            WHERE parent.reel_frame_id = ''
            """
        ).result_rows
        result[table] = _int(rows[0][0] if rows else 0)
    return result


def current_projection_metrics() -> dict[str, int]:
    client = clickhouse_client()
    row = client.query(
        """
        WITH latest AS
        (
            SELECT reel_frame_id,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id,
                   max(source_rank) AS source_rank
            FROM markorbit_facts.us_assignment_record_history
            GROUP BY reel_frame_id
        )
        SELECT count(), uniqExact(reel_frame_id), uniqExact(package_id)
        FROM latest
        """
    ).result_rows[0]
    property_row = client.query(
        """
        WITH latest AS
        (
            SELECT reel_frame_id,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
            FROM markorbit_facts.us_assignment_record_history
            GROUP BY reel_frame_id
        )
        SELECT count(),
               countIf(p.serial_number != '' AND (length(p.serial_number) != 8 OR NOT match(p.serial_number, '^[0-9]{8}$'))),
               countIf(p.serial_number != ''),
               countIf(p.serial_number != '' AND c.serial_number != '')
        FROM markorbit_facts.us_assignment_property_history AS p
        INNER JOIN latest AS l
          ON p.reel_frame_id = l.reel_frame_id
         AND toString(p.source_package_id) = l.package_id
        LEFT JOIN markorbit_facts.us_case_current FINAL AS c
          ON p.serial_number = c.serial_number AND c.is_deleted = 0
        """
    ).result_rows[0]
    return {
        "latest_record_count": _int(row[0]),
        "latest_reel_frame_count": _int(row[1]),
        "latest_source_package_count": _int(row[2]),
        "latest_property_count": _int(property_row[0]),
        "malformed_serial_count": _int(property_row[1]),
        "property_serial_count": _int(property_row[2]),
        "property_serial_joined_to_case_count": _int(property_row[3]),
    }


def _resolve_source_path(row: dict[str, Any], raw_root: Path) -> Path | None:
    for value in (row.get("file_path"), row.get("archived_path")):
        if value:
            path = Path(str(value))
            if path.is_file():
                return path
    file_name = str(row.get("file_name") or "")
    if file_name:
        for directory in (
            raw_root / "incoming" / "us_assignment",
            raw_root / "archive" / "us_assignment",
        ):
            path = directory / file_name
            if path.is_file():
                return path
    return None


def verify_source_files(packages: list[dict[str, Any]], raw_root: Path) -> dict[str, Any]:
    checked = 0
    missing: list[dict[str, str]] = []
    mismatched: list[dict[str, str]] = []
    for row in packages:
        path = _resolve_source_path(row, raw_root)
        if path is None:
            missing.append(
                {
                    "package_id": str(row["package_id"]),
                    "file_name": str(row["file_name"]),
                }
            )
            continue
        checked += 1
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = str(row.get("sha256") or "").lower()
        if digest.lower() != expected:
            mismatched.append(
                {
                    "package_id": str(row["package_id"]),
                    "file_name": str(row["file_name"]),
                    "expected_sha256": expected,
                    "actual_sha256": digest,
                }
            )
    return {
        "requested": True,
        "checked_count": checked,
        "missing_count": len(missing),
        "mismatch_count": len(mismatched),
        "missing": missing,
        "mismatched": mismatched,
    }


def evaluate_acceptance(
    *,
    packages: list[dict[str, Any]],
    schema: dict[str, Any],
    tables: dict[str, dict[str, int]],
    lineage: dict[str, list[dict[str, Any]]],
    orphans: dict[str, int],
    projection: dict[str, int],
    source_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    hard: list[str] = []
    not_ready: list[str] = []
    warnings: list[str] = []

    if not schema.get("ready"):
        not_ready.append("assignment_schema_not_ready")
    if not packages:
        not_ready.append("no_assignment_packages_registered")

    statuses = {str(row.get("status") or "") for row in packages}
    if statuses & {"FAILED", "MISSING_FILE"}:
        hard.append("failed_or_missing_assignment_source_packages")
    if statuses & {"REGISTERED", "PROCESSING", "INTERRUPTED"}:
        not_ready.append("assignment_ingestion_not_complete")

    successful = [row for row in packages if row.get("status") == "SUCCESS"]
    if packages and not successful:
        not_ready.append("no_successful_assignment_packages")
    old_profiles = [
        str(row["package_id"])
        for row in successful
        if str((row.get("profile") or {}).get("schema_version") or "")
        != ASSIGNMENT_SCHEMA_VERSION
    ]
    if old_profiles:
        not_ready.append("successful_assignment_packages_require_m10_replay")

    duplicates = {
        table: metrics["duplicate_observation_keys"]
        for table, metrics in tables.items()
        if metrics["duplicate_observation_keys"]
    }
    if duplicates:
        hard.append("duplicate_assignment_observation_keys")
    nonzero_orphans = {table: count for table, count in orphans.items() if count}
    if nonzero_orphans:
        hard.append("assignment_child_rows_without_record")

    registered_by_id = {str(row["package_id"]): row for row in packages}
    lineage_mismatches: list[dict[str, Any]] = []
    unknown_lineage: list[dict[str, Any]] = []
    for table, rows in lineage.items():
        for item in rows:
            package_id = str(item["package_id"])
            registered = registered_by_id.get(package_id)
            if registered is None:
                unknown_lineage.append({"table": table, **item})
                continue
            expected_rank = _int(registered.get("source_rank"))
            if (
                _int(item["min_source_rank"]) != expected_rank
                or _int(item["max_source_rank"]) != expected_rank
            ):
                lineage_mismatches.append(
                    {"table": table, "expected_source_rank": expected_rank, **item}
                )
    if unknown_lineage:
        hard.append("assignment_rows_reference_unknown_package")
    if lineage_mismatches:
        hard.append("assignment_source_lineage_rank_mismatch")

    record_rows = tables.get("us_assignment_record_history", {}).get("row_count", 0)
    if successful and record_rows == 0:
        hard.append("successful_assignment_packages_but_no_record_history")
    if projection.get("latest_record_count") != projection.get("latest_reel_frame_count"):
        hard.append("assignment_latest_projection_not_unique_by_reel_frame")
    if projection.get("malformed_serial_count"):
        warnings.append("malformed_assignment_property_serials_present")

    if source_verification is None:
        warnings.append("assignment_source_sha_verification_not_requested")
    else:
        if source_verification.get("missing_count"):
            hard.append("authoritative_assignment_source_files_missing")
        if source_verification.get("mismatch_count"):
            hard.append("authoritative_assignment_source_sha_mismatch")

    hard = list(dict.fromkeys(hard))
    not_ready = list(dict.fromkeys(not_ready))
    warnings = list(dict.fromkeys(warnings))
    if hard:
        status = "FAIL"
    elif not_ready:
        status = "NOT_READY"
    elif warnings:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    return {
        "audit": "US_ASSIGNMENT_M10_REAL_DATA_ACCEPTANCE",
        "audit_version": AUDIT_VERSION,
        "status": status,
        "hard_fail_reasons": hard,
        "not_ready_reasons": not_ready,
        "warning_reasons": warnings,
        "schema": schema,
        "package_count": len(packages),
        "successful_package_count": len(successful),
        "tables": tables,
        "orphans": orphans,
        "projection": projection,
        "lineage_mismatches": lineage_mismatches,
        "unknown_lineage": unknown_lineage,
        "source_verification": source_verification,
        "semantics": SEMANTICS,
        "legal_ownership_conclusion": False,
    }


def build_audit(
    *,
    raw_root: Path,
    verify_sources: bool = False,
) -> dict[str, Any]:
    packages = list_assignment_packages()
    verification = verify_source_files(packages, raw_root) if verify_sources else None
    return evaluate_acceptance(
        packages=packages,
        schema=schema_state(),
        tables=table_metrics(),
        lineage=lineage_metrics(),
        orphans=orphan_counts(),
        projection=current_projection_metrics(),
        source_verification=verification,
    )
