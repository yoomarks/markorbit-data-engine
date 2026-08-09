from __future__ import annotations

from typing import Any

from app.db import postgres_conn
from app.domain import DiscoveredPackage
from app.us.migrations import US_SCHEMA_VERSION
from app.us.package_meta import infer_us_package_descriptor


def register_us_package(package: DiscoveredPackage) -> tuple[str, bool]:
    descriptor = infer_us_package_descriptor(package.path)
    if descriptor.package_kind == "UNKNOWN":
        raise ValueError(f"Unknown USPTO package precedence: {package.file_name}")

    sql = """
    INSERT INTO control.source_package (
        jurisdiction, file_name, file_path, file_size, sha256,
        source_modified_at, package_kind, partition_dimension,
        partition_value, source_period_start, source_period_end,
        source_sequence, status, schema_version
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'REGISTERED', %s)
    ON CONFLICT (sha256)
    DO UPDATE SET
        file_name = EXCLUDED.file_name,
        file_path = EXCLUDED.file_path,
        file_size = EXCLUDED.file_size,
        source_modified_at = EXCLUDED.source_modified_at,
        package_kind = EXCLUDED.package_kind,
        partition_dimension = EXCLUDED.partition_dimension,
        partition_value = EXCLUDED.partition_value,
        source_period_start = EXCLUDED.source_period_start,
        source_period_end = EXCLUDED.source_period_end,
        source_sequence = EXCLUDED.source_sequence,
        schema_version = EXCLUDED.schema_version,
        status = CASE
            WHEN control.source_package.status = 'MISSING_FILE' THEN 'FAILED'
            ELSE control.source_package.status
        END,
        last_seen_at = now()
    RETURNING package_id, package_sequence, (xmax = 0) AS inserted
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    package.jurisdiction,
                    package.file_name,
                    str(package.path),
                    package.file_size,
                    package.sha256,
                    package.modified_at,
                    descriptor.package_kind,
                    descriptor.partition_dimension,
                    descriptor.partition_value,
                    descriptor.source_period_start,
                    descriptor.source_period_end,
                    descriptor.source_sequence,
                    US_SCHEMA_VERSION,
                ),
            )
            row = cur.fetchone()
            source_rank = descriptor.source_rank(int(row["package_sequence"]))
            cur.execute(
                "UPDATE control.source_package SET source_rank = %s WHERE package_id = %s",
                (source_rank, row["package_id"]),
            )
        conn.commit()
    return str(row["package_id"]), bool(row["inserted"])


def list_us_packages() -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, file_name, sha256, partition_dimension,
                       partition_value, source_rank, status
                FROM control.source_package
                WHERE jurisdiction = 'US'
                ORDER BY source_rank, package_sequence
                """
            )
            return [dict(row) for row in cur.fetchall()]


def list_us_blocking_failures() -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, file_name, source_rank, status
                FROM control.source_package
                WHERE jurisdiction = 'US'
                  AND status IN ('FAILED', 'MISSING_FILE')
                ORDER BY source_rank, package_sequence
                """
            )
            return [dict(row) for row in cur.fetchall()]
