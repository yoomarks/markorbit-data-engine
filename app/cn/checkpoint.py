from __future__ import annotations

import json
import uuid
from collections import Counter
from typing import Any

from app.db import clickhouse_client, postgres_conn


CHECKPOINT_VERSION = "CN_PACKAGE_MEMBER_CHECKPOINT_V2_SAFE_STAGE"
RESUMABLE_ROLES = {"goods", "priority", "madrid"}
STAGE_TABLES = (
    "markorbit_facts.cn_stage_basic",
    "markorbit_facts.cn_stage_applicant",
    "markorbit_facts.cn_stage_goods",
    "markorbit_facts.cn_stage_agent",
    "markorbit_facts.cn_stage_priority",
    "markorbit_facts.cn_stage_madrid",
    "markorbit_facts.cn_stage_coowner",
)
ROLE_STAGE_TABLE = {
    "basic": "markorbit_facts.cn_stage_basic",
    "applicant": "markorbit_facts.cn_stage_applicant",
    "goods": "markorbit_facts.cn_stage_goods",
    "agent": "markorbit_facts.cn_stage_agent",
    "priority": "markorbit_facts.cn_stage_priority",
    "madrid": "markorbit_facts.cn_stage_madrid",
    "coowner": "markorbit_facts.cn_stage_coowner",
}


def completed_member_names(package_id: str) -> set[str]:
    """Return metadata rows that currently exist for the package.

    During one live attempt this can include non-resumable members as well. On a
    subsequent retry validated_completed_member_names removes every row that is
    not a valid V2 checkpoint before cleanup/reuse decisions are made.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT internal_name
                FROM control.source_package_file
                WHERE package_id = %s
                """,
                (package_id,),
            )
            return {str(row["internal_name"]) for row in cur.fetchall()}


def _stage_rows_for_member(package_id: str, role: str, internal_name: str) -> int:
    table = ROLE_STAGE_TABLE.get(role)
    if table is None:
        return 0
    safe_package = str(uuid.UUID(package_id))
    escaped = internal_name.replace("'", "''")
    return int(
        clickhouse_client().query(
            f"""
            SELECT count()
            FROM {table}
            WHERE package_id = toUUID('{safe_package}')
              AND source_file = '{escaped}'
            """
        ).result_rows[0][0]
        or 0
    )


def validated_completed_member_names(package_id: str) -> set[str]:
    """Return only checkpoints whose exact retained stage is durable.

    V1 treated a source_package_file row as completion even though StageBatchWriter
    could still hold the member's last partial batch in Python memory. That made
    interrupted members look complete while ClickHouse contained only a prefix
    (for example 860000 staged applicant rows for 864720 source rows).

    V2 is deliberately conservative: only roles with no deferred entity/mention
    side effects are resumable, and a checkpoint is valid only when its stored
    exact stage-row count still matches ClickHouse. Old/unsafe/stale metadata is
    deleted so the member is reparsed on retry.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT internal_name, file_role, metrics
                FROM control.source_package_file
                WHERE package_id = %s
                """,
                (package_id,),
            )
            rows = list(cur.fetchall())

    if not rows:
        return set()

    valid: set[str] = set()
    stale: list[str] = []
    for row in rows:
        name = str(row["internal_name"])
        role = str(row["file_role"] or "")
        metrics = dict(row["metrics"] or {})
        if role not in RESUMABLE_ROLES:
            stale.append(name)
            continue
        if metrics.get("checkpoint_version") != CHECKPOINT_VERSION:
            stale.append(name)
            continue
        expected = metrics.get("checkpoint_stage_rows")
        if expected is None:
            stale.append(name)
            continue
        actual = _stage_rows_for_member(package_id, role, name)
        if actual == int(expected):
            valid.add(name)
        else:
            stale.append(name)

    if stale:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM control.source_package_file
                    WHERE package_id = %s AND internal_name = ANY(%s)
                    """,
                    (package_id, stale),
                )
            conn.commit()
    return valid


def checkpoint_role_counts(package_id: str) -> Counter[str]:
    """Rebuild role counts from the validated checkpoint metadata that remains."""
    result: Counter[str] = Counter()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_role, logical_rows
                FROM control.source_package_file
                WHERE package_id = %s
                  AND file_role IS NOT NULL
                """,
                (package_id,),
            )
            for row in cur.fetchall():
                result[str(row["file_role"])] += int(row["logical_rows"] or 0)
    return result


def record_member_checkpoint(package_id: str, item: dict[str, Any]) -> bool:
    """Mark a safe member resumable after its ClickHouse stage was flushed.

    Returns True when the member received a durable checkpoint. Party/basic/agent
    members are intentionally not resumable yet because entity/mention buffers
    have independent transactional side effects; they are reparsed after a crash
    rather than risking silent incompleteness.
    """
    role = str(item.get("role") or "")
    if role not in RESUMABLE_ROLES:
        return False

    stage_rows = _stage_rows_for_member(
        package_id,
        role,
        str(item["internal_name"]),
    )
    extra = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "checkpoint_stage_rows": stage_rows,
        "records_with_continuation": int(item.get("records_with_continuation", 0)),
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.source_package_file
                SET metrics = metrics || %s::jsonb
                WHERE package_id = %s AND internal_name = %s
                """,
                (json.dumps(extra), package_id, str(item["internal_name"])),
            )
        conn.commit()
    return True


def cleanup_uncheckpointed_stage(
    package_uuid: uuid.UUID,
    completed_members: set[str],
) -> None:
    """Delete rows that are not protected by validated completion metadata."""
    client = clickhouse_client()
    package = str(package_uuid)
    if completed_members:
        escaped = [name.replace("'", "''") for name in sorted(completed_members)]
        keep = ", ".join(f"'{name}'" for name in escaped)
        predicate = (
            f"package_id = toUUID('{package}') AND source_file NOT IN ({keep})"
        )
    else:
        predicate = f"package_id = toUUID('{package}')"

    for table in STAGE_TABLES:
        client.command(
            f"ALTER TABLE {table} DELETE WHERE {predicate} "
            "SETTINGS mutations_sync = 1"
        )


def _member_profiles(package_id: str) -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT internal_name, original_internal_name, file_role,
                       file_size, compressed_size, filename_encoding,
                       filename_repaired, content_encoding, header_raw,
                       header_canonical, physical_rows, logical_rows,
                       continuation_rows, repaired_rows, failed_rows,
                       replacement_chars, max_record_length, max_field_length,
                       metrics
                FROM control.source_package_file
                WHERE package_id = %s
                ORDER BY internal_name
                """,
                (package_id,),
            )
            rows = list(cur.fetchall())

    profiles: list[dict[str, Any]] = []
    for row in rows:
        metrics = dict(row["metrics"] or {})
        profiles.append(
            {
                "role": row["file_role"],
                "internal_name": row["internal_name"],
                "original_internal_name": row["original_internal_name"],
                "size": int(row["file_size"] or 0),
                "compressed_size": int(row["compressed_size"] or 0),
                "filename_encoding": row["filename_encoding"],
                "filename_repaired": bool(row["filename_repaired"]),
                "encoding": row["content_encoding"],
                "header_raw": row["header_raw"] or [],
                "header_canonical": row["header_canonical"] or [],
                "physical_rows": int(row["physical_rows"] or 0),
                "logical_rows": int(row["logical_rows"] or 0),
                "continuation_rows": int(row["continuation_rows"] or 0),
                "records_with_continuation": int(
                    metrics.get("records_with_continuation", 0)
                ),
                "replacement_chars": int(row["replacement_chars"] or 0),
                "mojibake_cells_repaired": int(
                    metrics.get("mojibake_cells_repaired", 0)
                ),
                "max_record_length": int(row["max_record_length"] or 0),
                "max_field_length": int(row["max_field_length"] or 0),
                "repairs": metrics.get("repairs", {}),
                "failed_rows": int(row["failed_rows"] or 0),
                "failed_examples": [],
            }
        )
    return profiles


def _stage_counts(package_id: str) -> dict[str, int]:
    client = clickhouse_client()
    safe = str(uuid.UUID(package_id))
    return {
        table: int(
            client.query(
                f"SELECT count() FROM {table} WHERE package_id = toUUID('{safe}')"
            ).result_rows[0][0]
        )
        for table in STAGE_TABLES
    }


def finalize_checkpoint_metrics(
    package_id: str,
    totals: dict[str, Any],
    *,
    reused_members: int,
) -> dict[str, Any]:
    """Correct final metrics/profile after a member-level resumed run."""
    profiles = _member_profiles(package_id)
    role_counts: Counter[str] = Counter()
    for item in profiles:
        if item.get("role"):
            role_counts[str(item["role"])] += int(item.get("logical_rows", 0))

    corrected = dict(totals)
    corrected["role_counts"] = dict(role_counts)
    corrected["stage_counts"] = _stage_counts(package_id)
    corrected["files"] = len(profiles)
    corrected["failed_rows"] = sum(int(item["failed_rows"]) for item in profiles)
    corrected["continuation_rows"] = sum(
        int(item["continuation_rows"]) for item in profiles
    )
    corrected["replacement_chars"] = sum(
        int(item["replacement_chars"]) for item in profiles
    )
    corrected["resume_checkpoint_version"] = CHECKPOINT_VERSION
    corrected["resume_checkpoint_members_reused"] = int(reused_members)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT profile FROM control.source_package WHERE package_id = %s",
                (package_id,),
            )
            row = cur.fetchone()
            profile = dict((row or {}).get("profile") or {})
            profile["members"] = profiles
            profile["totals"] = corrected
            profile["resume_checkpoint_version"] = CHECKPOINT_VERSION
            profile["resume_checkpoint_members_reused"] = int(reused_members)
            cur.execute(
                """
                UPDATE control.source_package
                SET profile = %s::jsonb, last_seen_at = now()
                WHERE package_id = %s
                """,
                (json.dumps(profile, ensure_ascii=False, default=str), package_id),
            )
            cur.execute(
                """
                UPDATE control.job_run
                SET metrics = %s::jsonb
                WHERE run_id = (
                    SELECT run_id
                    FROM control.job_run
                    WHERE job_type = 'CN_PACKAGE_INGESTION'
                      AND payload->>'package_id' = %s
                    ORDER BY started_at DESC
                    LIMIT 1
                )
                """,
                (
                    json.dumps(corrected, ensure_ascii=False, default=str),
                    package_id,
                ),
            )
        conn.commit()
    return corrected
