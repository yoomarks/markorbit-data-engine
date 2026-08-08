from __future__ import annotations

import json
import uuid
from collections import Counter
from typing import Any

from app.db import clickhouse_client, postgres_conn


CHECKPOINT_VERSION = "CN_PACKAGE_MEMBER_CHECKPOINT_V1"
STAGE_TABLES = (
    "markorbit_facts.cn_stage_basic",
    "markorbit_facts.cn_stage_applicant",
    "markorbit_facts.cn_stage_goods",
    "markorbit_facts.cn_stage_agent",
    "markorbit_facts.cn_stage_priority",
    "markorbit_facts.cn_stage_madrid",
    "markorbit_facts.cn_stage_coowner",
)


def completed_member_names(package_id: str) -> set[str]:
    """Return ZIP members that reached the durable per-member checkpoint."""
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


def checkpoint_role_counts(package_id: str) -> Counter[str]:
    """Rebuild raw role counts from durable completed-member profiles."""
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


def record_member_checkpoint(package_id: str, item: dict[str, Any]) -> None:
    """Augment source_package_file with resume-only profile details.

    upsert_package_file is already the durable commit marker and is called only
    after the member iterator finishes. This update stores details that are not
    first-class columns but are useful when reconstructing a final profile after
    a resumed run.
    """
    extra = {
        "checkpoint_version": CHECKPOINT_VERSION,
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


def cleanup_uncheckpointed_stage(
    package_uuid: uuid.UUID,
    completed_members: set[str],
) -> None:
    """Delete only rows that cannot be trusted after an interrupted process.

    Rows belonging to a member with a source_package_file checkpoint are known
    to have reached the end of that member and are retained. Rows from any other
    member may be a partial batch left by a killed Python/Docker/host process and
    are removed synchronously before retry.
    """
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
    """Correct final metrics/profile after a member-level resumed run.

    The legacy parser's in-memory counters only see members parsed in the current
    process. Durable source_package_file rows and retained stage rows cover both
    old and new members, so they are the authoritative source for final totals.
    """
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
