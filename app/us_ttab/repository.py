from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db import postgres_conn
from app.scanner import sha256_file
from app.us_ttab import TTAB_JURISDICTION, TTAB_SCHEMA_VERSION


VALID_SOURCE_KINDS = {"TTABVUE_PROCEEDING_RAWXML_SNAPSHOT"}
_SOURCE_RANK_BASE = 5_000_000_000_000_000_000
_SOURCE_RANK_MINOR_WIDTH = 1_000_000


def normalize_snapshot_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("TTAB snapshot_at must include timezone information")
    utc = value.astimezone(timezone.utc)
    # ClickHouse stores this source timestamp as DateTime64(3), so freeze the
    # registry/rank contract at the same millisecond precision.
    return utc.replace(microsecond=(utc.microsecond // 1000) * 1000)


def ttab_source_rank(snapshot_at: datetime, package_sequence: int) -> int:
    snapshot_at = normalize_snapshot_at(snapshot_at)
    if package_sequence <= 0 or package_sequence >= _SOURCE_RANK_MINOR_WIDTH:
        raise ValueError("TTAB package_sequence exceeds modeled source-rank minor range")
    epoch_millis = int(snapshot_at.timestamp() * 1000)
    return _SOURCE_RANK_BASE + epoch_millis * _SOURCE_RANK_MINOR_WIDTH + package_sequence


def register_ttab_source(
    path: Path,
    *,
    snapshot_at: datetime,
    source_kind: str = "TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
) -> tuple[str, bool]:
    source_kind = source_kind.strip().upper()
    if source_kind not in VALID_SOURCE_KINDS:
        raise ValueError(f"source_kind must be one of {sorted(VALID_SOURCE_KINDS)}")
    if path.suffix.lower() not in {".xml", ".zip"}:
        raise ValueError("US TTAB source must be .xml or .zip")
    snapshot_at = normalize_snapshot_at(snapshot_at)
    stat = path.stat()
    digest = sha256_file(path)
    partition_value = snapshot_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, jurisdiction, package_kind, partition_value
                FROM control.source_package WHERE sha256 = %s
                """,
                (digest,),
            )
            same_sha = cur.fetchone()
            if same_sha and same_sha["jurisdiction"] != TTAB_JURISDICTION:
                raise RuntimeError(
                    "The same SHA-256 is already registered under another jurisdiction; "
                    "refusing to relabel it as US_TTAB."
                )
            if same_sha and (
                same_sha["package_kind"] != source_kind
                or same_sha["partition_value"] != partition_value
            ):
                raise RuntimeError(
                    "The same US TTAB SHA-256 is already registered with different "
                    "source kind/snapshot-at metadata; source precedence is immutable."
                )

            cur.execute(
                """
                INSERT INTO control.source_package (
                    jurisdiction, file_name, file_path, file_size, sha256,
                    source_modified_at, package_kind, partition_dimension,
                    partition_value, source_period_start, source_period_end,
                    source_sequence, status, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'SNAPSHOT_AT',
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
                    TTAB_JURISDICTION,
                    path.name,
                    str(path),
                    stat.st_size,
                    digest,
                    datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    source_kind,
                    partition_value,
                    snapshot_at.date(),
                    snapshot_at.date(),
                    TTAB_SCHEMA_VERSION,
                ),
            )
            row = cur.fetchone()
            source_rank = ttab_source_rank(snapshot_at, int(row["package_sequence"]))
            cur.execute(
                "UPDATE control.source_package SET source_rank = %s WHERE package_id = %s",
                (source_rank, row["package_id"]),
            )
        conn.commit()
    return str(row["package_id"]), bool(row["inserted"])


def list_ttab_packages() -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, package_sequence, file_name, file_path, sha256,
                       package_kind, partition_dimension, partition_value,
                       source_period_start, source_period_end, source_rank, status,
                       profile, schema_version, archived_path, error_message
                FROM control.source_package
                WHERE jurisdiction = %s
                ORDER BY source_rank, package_sequence
                """,
                (TTAB_JURISDICTION,),
            )
            return [dict(row) for row in cur.fetchall()]


def list_ttab_blocking_failures() -> list[dict[str, Any]]:
    return [
        row for row in list_ttab_packages() if row["status"] in {"FAILED", "MISSING_FILE"}
    ]
