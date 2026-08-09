from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db import clickhouse_client, postgres_conn
from app.scanner import sha256_file
from app.us_ttab import TTAB_SCHEMA_VERSION, TTAB_SEMANTICS
from app.us_ttab.repository import list_ttab_packages


AUDIT_VERSION = "US_TTAB_M1.0_REAL_DATA_ACCEPTANCE_V1"
_TABLE_KEYS = {
    "us_ttab_proceeding_history": "observation_key",
    "us_ttab_party_history": "observation_key",
    "us_ttab_property_history": "observation_key",
    "us_ttab_docket_history": "observation_key",
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
            WHERE database = 'markorbit_facts' AND name LIKE 'us_ttab_%'
            """
        ).result_rows
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM control.schema_version WHERE component = 'US_TTAB'")
            row = cur.fetchone()
    postgres_version = str(row["version"]) if row else None
    clickhouse_versions = [
        str(row[0])
        for row in client.query(
            """
            SELECT version FROM markorbit_facts.schema_version FINAL
            WHERE component = 'US_TTAB'
            ORDER BY version
            """
        ).result_rows
    ]
    missing = sorted(set(_TABLE_KEYS) - tables)
    return {
        "expected": TTAB_SCHEMA_VERSION,
        "postgres": postgres_version,
        "clickhouse_versions": clickhouse_versions,
        "missing_tables": missing,
        "ready": (
            postgres_version == TTAB_SCHEMA_VERSION
            and TTAB_SCHEMA_VERSION in clickhouse_versions
            and not missing
        ),
    }


def table_metrics() -> dict[str, dict[str, int]]:
    client = clickhouse_client()
    result: dict[str, dict[str, int]] = {}
    for table, key in _TABLE_KEYS.items():
        row = client.query(
            f"""
            SELECT count(), uniqExact({key}), uniqExact(proceeding_number),
                   uniqExact(source_package_id)
            FROM markorbit_facts.{table}
            """
        ).result_rows[0]
        duplicate = client.query(
            f"""
            SELECT count() FROM
            (
                SELECT {key} FROM markorbit_facts.{table}
                GROUP BY {key} HAVING count() > 1
            )
            """
        ).result_rows[0][0]
        result[table] = {
            "row_count": _int(row[0]),
            "unique_observation_keys": _int(row[1]),
            "proceeding_count": _int(row[2]),
            "source_package_count": _int(row[3]),
            "duplicate_observation_keys": _int(duplicate),
        }
    return result


def orphan_counts() -> dict[str, int]:
    client = clickhouse_client()
    result: dict[str, int] = {}
    for table in (
        "us_ttab_party_history",
        "us_ttab_property_history",
        "us_ttab_docket_history",
    ):
        row = client.query(
            f"""
            SELECT count() FROM
            (
                SELECT DISTINCT proceeding_number, source_package_id
                FROM markorbit_facts.{table}
            ) AS child
            LEFT JOIN
            (
                SELECT DISTINCT proceeding_number, source_package_id
                FROM markorbit_facts.us_ttab_proceeding_history
            ) AS parent
            USING (proceeding_number, source_package_id)
            WHERE parent.proceeding_number = ''
            """
        ).result_rows[0][0]
        result[table] = _int(row)
    return result


def lineage_metrics() -> dict[str, dict[str, int]]:
    client = clickhouse_client()
    grouped: dict[str, list[tuple[str, int, int, int]]] = {}
    package_ids: set[str] = set()
    for table in _TABLE_KEYS:
        rows = [
            (str(row[0]), _int(row[1]), _int(row[2]), _int(row[3]))
            for row in client.query(
                f"""
                SELECT toString(source_package_id), min(source_rank), max(source_rank), count()
                FROM markorbit_facts.{table}
                GROUP BY source_package_id
                """
            ).result_rows
        ]
        grouped[table] = rows
        package_ids.update(row[0] for row in rows)

    registry: dict[str, dict[str, Any]] = {}
    if package_ids:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT package_id, jurisdiction, source_rank
                    FROM control.source_package
                    WHERE package_id::text = ANY(%s::text[])
                    """,
                    (sorted(package_ids),),
                )
                registry = {str(row["package_id"]): dict(row) for row in cur.fetchall()}

    result: dict[str, dict[str, int]] = {}
    for table, rows in grouped.items():
        missing = 0
        wrong_jurisdiction = 0
        rank_mismatch = 0
        for package_id, min_rank, max_rank, row_count in rows:
            meta = registry.get(package_id)
            if meta is None:
                missing += row_count
                continue
            if str(meta["jurisdiction"]) != "US_TTAB":
                wrong_jurisdiction += row_count
            expected_rank = _int(meta["source_rank"])
            if min_rank != expected_rank or max_rank != expected_rank:
                rank_mismatch += row_count
        result[table] = {
            "missing_registry_package_rows": missing,
            "wrong_jurisdiction_rows": wrong_jurisdiction,
            "source_rank_mismatch_rows": rank_mismatch,
        }
    return result


def projection_metrics() -> dict[str, int]:
    client = clickhouse_client()
    latest = client.query(
        """
        WITH latest AS
        (
            SELECT proceeding_number,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
            FROM markorbit_facts.us_ttab_proceeding_history
            GROUP BY proceeding_number
        )
        SELECT count(), uniqExact(proceeding_number), uniqExact(package_id)
        FROM latest
        """
    ).result_rows[0]
    properties = client.query(
        """
        WITH latest AS
        (
            SELECT proceeding_number,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
            FROM markorbit_facts.us_ttab_proceeding_history
            GROUP BY proceeding_number
        )
        SELECT count(), countIf(p.serial_number != ''),
               countIf(p.serial_number != '' AND (length(p.serial_number) != 8 OR NOT match(p.serial_number, '^[0-9]{8}$'))),
               countIf(p.serial_number != '' AND c.serial_number != '')
        FROM markorbit_facts.us_ttab_property_history AS p
        INNER JOIN latest AS l
          ON p.proceeding_number = l.proceeding_number
         AND toString(p.source_package_id) = l.package_id
        LEFT JOIN
        (
            SELECT serial_number
            FROM markorbit_facts.us_case_current FINAL
            WHERE is_deleted = 0
        ) AS c
          ON p.serial_number = c.serial_number
        """
    ).result_rows[0]
    docket = client.query(
        """
        WITH latest AS
        (
            SELECT proceeding_number,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
            FROM markorbit_facts.us_ttab_proceeding_history
            GROUP BY proceeding_number
        )
        SELECT count(), countIf(d.due_date IS NOT NULL OR d.due_date_raw != '')
        FROM markorbit_facts.us_ttab_docket_history AS d
        INNER JOIN latest AS l
          ON d.proceeding_number = l.proceeding_number
         AND toString(d.source_package_id) = l.package_id
        """
    ).result_rows[0]
    return {
        "latest_projection_count": _int(latest[0]),
        "latest_proceeding_count": _int(latest[1]),
        "latest_source_package_count": _int(latest[2]),
        "latest_property_count": _int(properties[0]),
        "property_serial_count": _int(properties[1]),
        "malformed_property_serial_count": _int(properties[2]),
        "property_serial_joined_to_us_case_count": _int(properties[3]),
        "latest_docket_count": _int(docket[0]),
        "due_date_observation_count": _int(docket[1]),
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
            raw_root / "incoming" / "us_ttab",
            raw_root / "archive" / "us_ttab",
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
        actual = sha256_file(path).lower()
        expected = str(row.get("sha256") or "").lower()
        if actual != expected:
            mismatched.append(
                {
                    "package_id": str(row["package_id"]),
                    "file_name": str(row["file_name"]),
                    "expected_sha256": expected,
                    "actual_sha256": actual,
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
    orphans: dict[str, int],
    lineage: dict[str, dict[str, int]],
    projection: dict[str, int],
    source_verification: dict[str, Any] | None,
    verify_sources: bool,
) -> dict[str, Any]:
    hard: list[str] = []
    not_ready: list[str] = []
    warnings: list[str] = []
    if not schema.get("ready"):
        hard.append("ttab_schema_not_ready")
    if not packages:
        not_ready.append("no_ttab_packages_registered")
    statuses = {str(row.get("status") or "") for row in packages}
    if statuses & {"FAILED", "MISSING_FILE", "INTERRUPTED"}:
        hard.append("ttab_package_failure_or_interruption_present")
    if statuses & {"REGISTERED", "PROCESSING"}:
        not_ready.append("ttab_ingestion_not_complete")
    successful = [row for row in packages if row.get("status") == "SUCCESS"]
    if packages and not successful and not not_ready:
        not_ready.append("no_successful_ttab_packages")
    if any(
        str(row.get("schema_version") or "") != TTAB_SCHEMA_VERSION
        for row in successful
    ):
        not_ready.append("successful_ttab_packages_require_m10_replay")
    for table, metrics in tables.items():
        if metrics["duplicate_observation_keys"]:
            hard.append(f"duplicate_observation_keys:{table}")
    for table, count in orphans.items():
        if count:
            hard.append(f"orphan_snapshot_children:{table}")
    for table, metrics in lineage.items():
        if any(metrics.values()):
            hard.append(f"source_lineage_mismatch:{table}")
    if successful and projection.get("latest_proceeding_count", 0) == 0:
        hard.append("successful_ttab_packages_but_no_proceeding_history")
    if (
        projection
        and projection["latest_projection_count"] != projection["latest_proceeding_count"]
    ):
        hard.append("latest_ttab_projection_not_unique")
    if projection.get("malformed_property_serial_count", 0):
        warnings.append("malformed_ttab_property_serials_present")
    if projection.get("property_serial_count", 0) > projection.get(
        "property_serial_joined_to_us_case_count", 0
    ):
        warnings.append("some_ttab_property_serials_not_present_in_us_case_current")
    if projection.get("latest_docket_count", 0) == 0 and projection.get(
        "latest_proceeding_count", 0
    ):
        warnings.append("some_or_all_ttab_snapshots_have_no_docket_rows")
    if verify_sources:
        if source_verification is None:
            hard.append("ttab_source_verification_missing")
        elif source_verification["missing_count"]:
            hard.append("ttab_source_files_missing")
        elif source_verification["mismatch_count"]:
            hard.append("ttab_source_sha256_mismatch")
    else:
        warnings.append("ttab_source_sha_verification_not_requested")
    status = (
        "FAIL"
        if hard
        else "NOT_READY"
        if not_ready
        else "PASS_WITH_WARNINGS"
        if warnings
        else "PASS"
    )
    return {
        "status": status,
        "hard_fail_reasons": sorted(set(hard)),
        "not_ready_reasons": sorted(set(not_ready)),
        "warning_reasons": sorted(set(warnings)),
    }


def build_audit(*, raw_root: Path, verify_sources: bool = False) -> dict[str, Any]:
    packages = list_ttab_packages()
    schema = schema_state()
    tables = table_metrics() if schema["ready"] else {}
    orphans = orphan_counts() if schema["ready"] else {}
    lineage = lineage_metrics() if schema["ready"] else {}
    projection = projection_metrics() if schema["ready"] else {}
    verification = verify_source_files(packages, raw_root) if verify_sources else None
    decision = evaluate_acceptance(
        packages=packages,
        schema=schema,
        tables=tables,
        orphans=orphans,
        lineage=lineage,
        projection=projection,
        source_verification=verification,
        verify_sources=verify_sources,
    )
    return {
        "audit_version": AUDIT_VERSION,
        **decision,
        "schema": schema,
        "package_count": len(packages),
        "packages": packages,
        "tables": tables,
        "orphan_counts": orphans,
        "lineage": lineage,
        "projection": projection,
        "source_verification": verification,
        "semantics": TTAB_SEMANTICS,
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }
