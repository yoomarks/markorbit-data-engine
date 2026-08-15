from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid
from typing import Any

from app.db import clickhouse_client, postgres_conn
from app.repository import (
    create_job_run,
    finish_job_run,
    get_package,
    record_quality_issues,
    update_package_status,
)


CHECKPOINT_VERSION = "CN_M16_STAGE_V1"
CHECKPOINT_MAX_AGE = timedelta(hours=120)
STAGE_TABLES = (
    "markorbit_facts.cn_stage_basic",
    "markorbit_facts.cn_stage_applicant",
    "markorbit_facts.cn_stage_goods",
    "markorbit_facts.cn_stage_agent",
    "markorbit_facts.cn_stage_priority",
    "markorbit_facts.cn_stage_madrid",
    "markorbit_facts.cn_stage_coowner",
)


def ensure_stage_checkpoint_schema() -> None:
    """Create the checkpoint table on existing development volumes as well as fresh ones."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS control.cn_package_stage_checkpoint (
                    package_id uuid PRIMARY KEY
                        REFERENCES control.source_package(package_id) ON DELETE CASCADE,
                    checkpoint_version text NOT NULL,
                    source_sha256 char(64) NOT NULL,
                    snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
                    staged_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_cn_package_stage_checkpoint_updated
                ON control.cn_package_stage_checkpoint(updated_at DESC)
                """
            )
        conn.commit()


def _package_file_profiles(package_id: str) -> list[dict[str, Any]]:
    sql = """
    SELECT internal_name, original_internal_name, file_role, file_size,
           compressed_size, filename_encoding, filename_repaired,
           content_encoding, header_raw, header_canonical, physical_rows,
           logical_rows, continuation_rows, repaired_rows, failed_rows,
           replacement_chars, max_record_length, max_field_length, metrics
    FROM control.source_package_file
    WHERE package_id = %s
    ORDER BY internal_name
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (package_id,))
            rows = list(cur.fetchall())

    profiles: list[dict[str, Any]] = []
    for row in rows:
        metrics = dict(row.get("metrics") or {})
        profiles.append(
            {
                "role": row.get("file_role") or "",
                "internal_name": row["internal_name"],
                "encoding": row.get("content_encoding") or "",
                # Delimiter and failed examples are not persisted in the legacy
                # package-file table. They are diagnostic only; preserve the
                # durable counters rather than reparsing a multi-GB ZIP merely
                # to reconstruct presentation metadata.
                "delimiter": "",
                "header_raw": list(row.get("header_raw") or []),
                "header_canonical": list(row.get("header_canonical") or []),
                "physical_rows": int(row.get("physical_rows") or 0),
                "logical_rows": int(row.get("logical_rows") or 0),
                "continuation_rows": int(row.get("continuation_rows") or 0),
                "records_with_continuation": 0,
                "replacement_chars": int(row.get("replacement_chars") or 0),
                "mojibake_cells_repaired": int(
                    metrics.get("mojibake_cells_repaired") or 0
                ),
                "max_record_length": int(row.get("max_record_length") or 0),
                "max_field_length": int(row.get("max_field_length") or 0),
                "repairs": dict(metrics.get("repairs") or {}),
                "failed_rows": int(row.get("failed_rows") or 0),
                "failed_examples": [],
                "original_internal_name": row["original_internal_name"],
                "size": int(row.get("file_size") or 0),
                "compressed_size": int(row.get("compressed_size") or 0),
                "filename_repaired": bool(row.get("filename_repaired") or False),
                "filename_encoding": row.get("filename_encoding") or "",
                "checkpoint_profile_reconstructed": True,
            }
        )
    return profiles


def _stage_counts(package_uuid: uuid.UUID, client: Any) -> dict[str, int]:
    package = str(package_uuid)
    expressions = [
        f"(SELECT count() FROM {table} PREWHERE package_id = toUUID('{package}'))"
        f" AS c{index}"
        for index, table in enumerate(STAGE_TABLES)
    ]
    rows = client.query("SELECT " + ", ".join(expressions)).result_rows
    values = rows[0] if rows else tuple(0 for _ in STAGE_TABLES)
    return {table: int(values[index] or 0) for index, table in enumerate(STAGE_TABLES)}


def capture_stage_snapshot(
    package_uuid: uuid.UUID,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    client = client or clickhouse_client()
    profiles = _package_file_profiles(str(package_uuid))
    role_counts: Counter[str] = Counter()
    for profile in profiles:
        role = str(profile.get("role") or "")
        if role:
            role_counts[role] += int(profile.get("logical_rows") or 0)

    stage_counts = _stage_counts(package_uuid, client)
    if stage_counts["markorbit_facts.cn_stage_basic"] <= 0:
        raise RuntimeError("Cannot checkpoint CN stage: BASIC stage is empty")
    if stage_counts["markorbit_facts.cn_stage_goods"] <= 0:
        raise RuntimeError("Cannot checkpoint CN stage: GOODS stage is empty")

    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "stage_counts": stage_counts,
        "role_counts": dict(role_counts),
        "member_profiles": profiles,
    }


def save_stage_checkpoint(
    package_uuid: uuid.UUID,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    package = get_package(str(package_uuid))
    snapshot = capture_stage_snapshot(package_uuid, client=client)
    source_sha256 = str(package.get("sha256") or "")
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO control.cn_package_stage_checkpoint (
                    package_id, checkpoint_version, source_sha256, snapshot,
                    staged_at, updated_at
                )
                VALUES (%s, %s, %s, %s::jsonb, now(), now())
                ON CONFLICT (package_id)
                DO UPDATE SET
                    checkpoint_version = EXCLUDED.checkpoint_version,
                    source_sha256 = EXCLUDED.source_sha256,
                    snapshot = EXCLUDED.snapshot,
                    staged_at = now(),
                    updated_at = now()
                """,
                (
                    str(package_uuid),
                    CHECKPOINT_VERSION,
                    source_sha256,
                    json.dumps(snapshot, ensure_ascii=False, default=str),
                ),
            )
        conn.commit()
    return snapshot


def load_stage_checkpoint(package_id: str) -> dict[str, Any] | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.checkpoint_version, c.source_sha256, c.snapshot,
                       c.staged_at, c.updated_at, p.sha256 AS package_sha256
                FROM control.cn_package_stage_checkpoint AS c
                JOIN control.source_package AS p ON p.package_id = c.package_id
                WHERE c.package_id = %s
                """,
                (package_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    if str(row["checkpoint_version"]) != CHECKPOINT_VERSION:
        return None
    if str(row["source_sha256"]) != str(row["package_sha256"]):
        return None
    staged_at = row["staged_at"]
    now = datetime.now(timezone.utc)
    if staged_at is None or now - staged_at > CHECKPOINT_MAX_AGE:
        return None
    return {
        "checkpoint_version": str(row["checkpoint_version"]),
        "source_sha256": str(row["source_sha256"]),
        "snapshot": dict(row.get("snapshot") or {}),
        "staged_at": staged_at,
        "updated_at": row["updated_at"],
    }


def clear_stage_checkpoint(package_id: str) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM control.cn_package_stage_checkpoint WHERE package_id = %s",
                (package_id,),
            )
        conn.commit()


def stage_checkpoint_is_usable(
    package_uuid: uuid.UUID,
    checkpoint: dict[str, Any],
    *,
    client: Any | None = None,
) -> bool:
    """Require exact durable stage counts before skipping raw ZIP parsing.

    CN stage tables have a seven-day TTL. Exact count validation makes a stale,
    partially deleted, manually changed, or interrupted cleanup checkpoint fail
    closed instead of publishing an incomplete package.
    """
    snapshot = dict(checkpoint.get("snapshot") or {})
    expected = {
        str(table): int(count or 0)
        for table, count in dict(snapshot.get("stage_counts") or {}).items()
    }
    if set(expected) != set(STAGE_TABLES):
        return False
    if expected.get("markorbit_facts.cn_stage_basic", 0) <= 0:
        return False
    if expected.get("markorbit_facts.cn_stage_goods", 0) <= 0:
        return False
    actual = _stage_counts(package_uuid, client or clickhouse_client())
    return actual == expected


def _rehydrated_file_quality_issues(
    package_uuid: uuid.UUID,
    run_id: uuid.UUID,
    profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for profile in profiles:
        source_file = str(profile.get("internal_name") or "")
        unknown_headers = [
            value
            for value in list(profile.get("header_canonical") or [])
            if str(value).startswith("unknown:")
        ]
        if unknown_headers:
            issues.append(
                {
                    "package_id": package_uuid,
                    "run_id": run_id,
                    "issue_type": "UNKNOWN_SOURCE_HEADER",
                    "severity": "WARNING",
                    "source_file": source_file,
                    "source_row": 1,
                    "raw_excerpt": ",".join(
                        str(value) for value in list(profile.get("header_raw") or [])
                    ),
                    "details": {
                        "unknown_headers": unknown_headers,
                        "canonical_headers": list(profile.get("header_canonical") or []),
                        "recovered_from_stage_checkpoint": True,
                    },
                }
            )
        failed_rows = int(profile.get("failed_rows") or 0)
        if failed_rows:
            issues.append(
                {
                    "package_id": package_uuid,
                    "run_id": run_id,
                    "issue_type": "UNREPAIRABLE_CSV_ROW",
                    "severity": "ERROR",
                    "occurrence_count": failed_rows,
                    "source_file": source_file,
                    "source_row": None,
                    "raw_excerpt": "",
                    "details": {
                        "failed_rows": failed_rows,
                        "recovered_from_stage_checkpoint": True,
                    },
                }
            )
        replacement_chars = int(profile.get("replacement_chars") or 0)
        if replacement_chars:
            issues.append(
                {
                    "package_id": package_uuid,
                    "run_id": run_id,
                    "issue_type": "INVALID_TEXT_BYTES_REPLACED",
                    "severity": "WARNING",
                    "occurrence_count": replacement_chars,
                    "source_file": source_file,
                    "source_row": None,
                    "raw_excerpt": "",
                    "details": {
                        "replacement_chars": replacement_chars,
                        "recovered_from_stage_checkpoint": True,
                    },
                }
            )
    return issues


def resume_staged_package(
    legacy_module: Any,
    package_id: str,
    path: Path,
    raw_root: Path,
    checkpoint: dict[str, Any],
    *,
    trigger_type: str,
    cleanup_stage: Any,
) -> dict[str, Any]:
    """Resume at post-stage quality/publish without reparsing the raw package."""
    package_uuid = uuid.UUID(str(package_id))
    package_meta = get_package(str(package_uuid))
    snapshot = dict(checkpoint.get("snapshot") or {})
    member_profiles = list(snapshot.get("member_profiles") or [])
    stage_counts = {
        str(table): int(value or 0)
        for table, value in dict(snapshot.get("stage_counts") or {}).items()
    }
    role_counts = {
        str(role): int(value or 0)
        for role, value in dict(snapshot.get("role_counts") or {}).items()
    }

    run_id_text = create_job_run(
        job_type="CN_PACKAGE_INGESTION",
        trigger_type=trigger_type,
        payload={
            "package_id": str(package_uuid),
            "path": str(path),
            "package_kind": package_meta["package_kind"],
            "source_rank": package_meta["source_rank"],
            "stage_resume": True,
            "stage_checkpoint_version": CHECKPOINT_VERSION,
        },
    )
    run_id = uuid.UUID(run_id_text)

    try:
        update_package_status(
            str(package_uuid),
            "PROCESSING",
            package_kind=str(package_meta["package_kind"]),
        )
        legacy_module._cleanup_partial_outputs(package_uuid)

        quality_issues = _rehydrated_file_quality_issues(
            package_uuid,
            run_id,
            member_profiles,
        )
        quality_issues.extend(
            legacy_module._collect_stage_quality_issues(package_uuid, run_id)
        )
        if quality_issues:
            record_quality_issues(quality_issues)

        publish_metrics = legacy_module._publish(package_uuid, package_meta)
        totals = {
            "role_counts": role_counts,
            "stage_counts": stage_counts,
            "files": len(member_profiles),
            "failed_rows": sum(int(item.get("failed_rows") or 0) for item in member_profiles),
            "continuation_rows": sum(
                int(item.get("continuation_rows") or 0) for item in member_profiles
            ),
            "replacement_chars": sum(
                int(item.get("replacement_chars") or 0) for item in member_profiles
            ),
            "package_kind": package_meta["package_kind"],
            "partition_dimension": package_meta["partition_dimension"],
            "partition_value": package_meta["partition_value"],
            "source_rank": package_meta["source_rank"],
            "publish": publish_metrics,
            "cn_stage_resume_used": True,
            "cn_stage_checkpoint_version": CHECKPOINT_VERSION,
        }
        profile = {
            "schema_version": "M1.5",
            "package_kind": package_meta["package_kind"],
            "partition_dimension": package_meta["partition_dimension"],
            "partition_value": package_meta["partition_value"],
            "source_period_start": package_meta.get("source_period_start"),
            "source_period_end": package_meta.get("source_period_end"),
            "source_rank": package_meta["source_rank"],
            "members": member_profiles,
            "totals": totals,
        }
        archived = legacy_module._archive_package(path, raw_root)
        update_package_status(
            str(package_uuid),
            "SUCCESS",
            package_kind=str(package_meta["package_kind"]),
            profile=profile,
            archived_path=str(archived),
        )
        finish_job_run(run_id_text, "SUCCESS", metrics=totals)
        cleanup_stage(package_uuid)
        clear_stage_checkpoint(str(package_uuid))
        return totals
    except Exception as exc:
        try:
            legacy_module._cleanup_partial_outputs(package_uuid)
        except Exception:
            pass
        update_package_status(
            str(package_uuid),
            "FAILED",
            package_kind=str(package_meta["package_kind"]),
            error_message=str(exc),
        )
        finish_job_run(run_id_text, "FAILED", error_message=str(exc))
        raise
