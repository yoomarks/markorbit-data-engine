from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.db import postgres_conn
from app.scanner import sha256_file
from app.us_assignment import ASSIGNMENT_JURISDICTION, ASSIGNMENT_SCHEMA_VERSION


VALID_SOURCE_KINDS = {"DAILY_ASSIGNMENT_XML", "ASSIGNMENT_SNAPSHOT_XML"}
_SOURCE_RANK_BASE = 3_000_000_000_000_000_000
_SOURCE_RANK_MINOR_WIDTH = 1_000_000_000


def assignment_source_rank(effective_date: date, package_sequence: int) -> int:
    if package_sequence <= 0 or package_sequence >= _SOURCE_RANK_MINOR_WIDTH:
        raise ValueError("assignment package_sequence exceeds modeled source-rank minor range")
    major = int(effective_date.strftime("%Y%m%d"))
    return _SOURCE_RANK_BASE + major * _SOURCE_RANK_MINOR_WIDTH + package_sequence


def register_assignment_source(
    path: Path,
    *,
    effective_date: date,
    source_kind: str,
) -> tuple[str, bool]:
    source_kind = source_kind.strip().upper()
    if source_kind not in VALID_SOURCE_KINDS:
        raise ValueError(f"source_kind must be one of {sorted(VALID_SOURCE_KINDS)}")
    if path.suffix.lower() not in {".xml", ".zip"}:
        raise ValueError("US assignment source must be .xml or .zip")
    stat = path.stat()
    digest = sha256_file(path)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, jurisdiction, package_kind, partition_value
                FROM control.source_package
                WHERE sha256 = %s
                """,
                (digest,),
            )
            same_sha = cur.fetchone()
            if same_sha and same_sha["jurisdiction"] != ASSIGNMENT_JURISDICTION:
                raise RuntimeError(
                    "The same SHA-256 is already registered under another jurisdiction; "
                    "refusing to relabel an existing source package as US_ASSIGNMENT."
                )
            if same_sha and (
                same_sha["package_kind"] != source_kind
                or same_sha["partition_value"] != effective_date.isoformat()
            ):
                raise RuntimeError(
                    "The same US assignment SHA-256 is already registered with different "
                    "source kind/effective-date metadata; source precedence is immutable."
                )

            cur.execute(
                """
                SELECT package_id, sha256
                FROM control.source_package
                WHERE jurisdiction = %s
                  AND package_kind = %s
                  AND partition_dimension = 'DELIVERY_DATE'
                  AND partition_value = %s
                  AND sha256 <> %s
                LIMIT 1
                """,
                (
                    ASSIGNMENT_JURISDICTION,
                    source_kind,
                    effective_date.isoformat(),
                    digest,
                ),
            )
            conflict = cur.fetchone()
            if conflict:
                raise RuntimeError(
                    "A different US assignment source is already registered for the same "
                    f"source kind/effective date ({source_kind} {effective_date}). "
                    "Revision precedence is not modeled; register only the authoritative file."
                )

            cur.execute(
                """
                INSERT INTO control.source_package (
                    jurisdiction, file_name, file_path, file_size, sha256,
                    source_modified_at, package_kind, partition_dimension,
                    partition_value, source_period_start, source_period_end,
                    source_sequence, status, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'DELIVERY_DATE',
                        %s, %s, %s, 0, 'REGISTERED', %s)
                ON CONFLICT (sha256)
                DO UPDATE SET
                    file_name = EXCLUDED.file_name,
                    file_path = EXCLUDED.file_path,
                    file_size = EXCLUDED.file_size,
                    source_modified_at = EXCLUDED.source_modified_at,
                    schema_version = EXCLUDED.schema_version,
                    status = CASE
                        WHEN control.source_package.status = 'MISSING_FILE' THEN 'FAILED'
                        ELSE control.source_package.status
                    END,
                    last_seen_at = now()
                RETURNING package_id, package_sequence, (xmax = 0) AS inserted
                """,
                (
                    ASSIGNMENT_JURISDICTION,
                    path.name,
                    str(path),
                    stat.st_size,
                    digest,
                    datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    source_kind,
                    effective_date.isoformat(),
                    effective_date,
                    effective_date,
                    ASSIGNMENT_SCHEMA_VERSION,
                ),
            )
            row = cur.fetchone()
            source_rank = assignment_source_rank(
                effective_date,
                int(row["package_sequence"]),
            )
            cur.execute(
                "UPDATE control.source_package SET source_rank = %s WHERE package_id = %s",
                (source_rank, row["package_id"]),
            )
        conn.commit()
    return str(row["package_id"]), bool(row["inserted"])


def list_assignment_packages() -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, package_sequence, file_name, file_path, sha256,
                       package_kind, partition_value, source_period_start,
                       source_period_end, source_rank, status, profile, archived_path,
                       error_message
                FROM control.source_package
                WHERE jurisdiction = %s
                ORDER BY source_rank, package_sequence
                """,
                (ASSIGNMENT_JURISDICTION,),
            )
            return [dict(row) for row in cur.fetchall()]


def list_assignment_blocking_failures() -> list[dict[str, Any]]:
    return [
        row
        for row in list_assignment_packages()
        if row["status"] in {"FAILED", "MISSING_FILE"}
    ]
