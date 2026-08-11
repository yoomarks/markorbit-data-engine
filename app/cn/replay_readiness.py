from __future__ import annotations

import argparse
import json
from typing import Any

from app.cn.goods_lifecycle import REQUIRED_M16_GOODS_COLUMNS
from app.db import clickhouse_client, postgres_conn
from app.version import engine_version


READINESS_VERSION = "CN_M16_REPLAY_READINESS_V1"

_PACKAGE_STATUS_SQL = """
SELECT status, count(*)
FROM control.source_package
WHERE jurisdiction = 'CN'
GROUP BY status
ORDER BY status
"""

_NEXT_PENDING_SQL = """
SELECT package_id, file_name, package_kind, partition_value, source_rank, status
FROM control.source_package
WHERE jurisdiction = 'CN'
  AND status IN ('INTERRUPTED', 'REGISTERED')
ORDER BY source_rank, package_sequence
LIMIT 1
"""

_NEXT_RETRY_SQL = """
SELECT package_id, file_name, package_kind, partition_value, source_rank, status, error_message
FROM control.source_package
WHERE jurisdiction = 'CN'
  AND status IN ('FAILED', 'MISSING_FILE')
ORDER BY source_rank, package_sequence
LIMIT 1
"""

_LATEST_SUCCESS_SQL = """
SELECT package_id, file_name, package_kind, partition_value, source_rank, processed_at
FROM control.source_package
WHERE jurisdiction = 'CN'
  AND status = 'SUCCESS'
ORDER BY processed_at DESC NULLS LAST, package_sequence DESC
LIMIT 1
"""

_SCHEMA_SQL = """
SELECT table, name
FROM system.columns
WHERE database = 'markorbit_facts'
"""

_STORAGE_SQL = """
SELECT
    countIf(transition_type IN ('FIRST_OBSERVED', 'REOBSERVED'))
FROM markorbit_facts.cn_goods_item_observation
"""

_EVENT_BASELINE_SQL = """
SELECT countIf(
    event_type IN ('APPLICATION_OBSERVED', 'GOODS_SCOPE_OBSERVED', 'DERIVED_CASE_OBSERVED')
    OR (
        event_type IN (
            'PRELIMINARY_PUBLICATION_OBSERVED',
            'REGISTRATION_PUBLICATION_OBSERVED',
            'EXCLUSIVE_TERM_OBSERVED'
        )
        AND old_value_compact = ''
    )
)
FROM markorbit_facts.cn_observed_event
"""

_PARTY_HISTORY_SQL = """
SELECT count()
FROM markorbit_facts.cn_case_party_relation_history
"""

_SHADOW_SQL = """
SELECT name
FROM system.tables
WHERE database = 'markorbit_facts'
  AND name LIKE '%storage_v2_shadow%'
ORDER BY name
"""

_PENDING_MUTATIONS_SQL = """
SELECT table, mutation_id, command
FROM system.mutations
WHERE database = 'markorbit_facts'
  AND is_done = 0
ORDER BY table, create_time
"""

_STAGE_SQL = """
SELECT sum(rows)
FROM system.parts
WHERE database = 'markorbit_facts'
  AND active
  AND table LIKE 'cn_stage_%'
"""

_PARTS_SQL = """
SELECT sum(bytes_on_disk), sum(rows)
FROM system.parts
WHERE database = 'markorbit_facts'
  AND active
"""

_DISK_SQL = """
SELECT name, path, free_space, total_space
FROM system.disks
ORDER BY name
"""


def _dict_or_none(row: Any | None, columns: tuple[str, ...]) -> dict[str, Any] | None:
    if row is None:
        return None
    return {column: value for column, value in zip(columns, row)}


def _package_state() -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_PACKAGE_STATUS_SQL)
            status_counts = {str(status): int(count or 0) for status, count in cur.fetchall()}

            cur.execute(_NEXT_PENDING_SQL)
            next_pending = _dict_or_none(
                cur.fetchone(),
                (
                    "package_id",
                    "file_name",
                    "package_kind",
                    "partition_value",
                    "source_rank",
                    "status",
                ),
            )

            cur.execute(_NEXT_RETRY_SQL)
            next_retry = _dict_or_none(
                cur.fetchone(),
                (
                    "package_id",
                    "file_name",
                    "package_kind",
                    "partition_value",
                    "source_rank",
                    "status",
                    "error_message",
                ),
            )

            cur.execute(_LATEST_SUCCESS_SQL)
            latest_success = _dict_or_none(
                cur.fetchone(),
                (
                    "package_id",
                    "file_name",
                    "package_kind",
                    "partition_value",
                    "source_rank",
                    "processed_at",
                ),
            )

    return {
        "status_counts": status_counts,
        "registered_package_count": sum(status_counts.values()),
        "next_pending": next_pending,
        "next_retry": next_retry,
        "latest_success": latest_success,
    }


def _scalar(client: Any, sql: str) -> int:
    return int(client.query(sql).result_rows[0][0] or 0)


def _storage_state() -> dict[str, Any]:
    client = clickhouse_client()
    available = {(str(table), str(name)) for table, name in client.query(_SCHEMA_SQL).result_rows}
    missing_schema = sorted(
        f"{table}.{column}" for table, column in REQUIRED_M16_GOODS_COLUMNS - available
    )

    shadow_tables = [str(row[0]) for row in client.query(_SHADOW_SQL).result_rows]
    pending_mutations = [
        {"table": str(table), "mutation_id": str(mutation_id), "command": str(command)}
        for table, mutation_id, command in client.query(_PENDING_MUTATIONS_SQL).result_rows
    ]
    active_bytes, active_rows = client.query(_PARTS_SQL).result_rows[0]
    disks = [
        {
            "name": str(name),
            "path": str(path),
            "free_space": int(free_space or 0),
            "total_space": int(total_space or 0),
        }
        for name, path, free_space, total_space in client.query(_DISK_SQL).result_rows
    ]

    return {
        "missing_m16_schema": missing_schema,
        "goods_baseline_history_rows": _scalar(client, _STORAGE_SQL),
        "reconstructible_event_baseline_rows": _scalar(client, _EVENT_BASELINE_SQL),
        "legacy_party_history_rows": _scalar(client, _PARTY_HISTORY_SQL),
        "active_stage_rows": _scalar(client, _STAGE_SQL),
        "storage_v2_shadow_tables": shadow_tables,
        "pending_mutations": pending_mutations,
        "active_bytes": int(active_bytes or 0),
        "active_rows": int(active_rows or 0),
        "disks": disks,
    }


def evaluate_readiness(
    *,
    package_state: dict[str, Any],
    storage_state: dict[str, Any],
    persistent_worker_running: bool,
    current_engine_version: str,
) -> dict[str, Any]:
    counts = package_state.get("status_counts") or {}
    failed_count = int(counts.get("FAILED") or 0)
    missing_count = int(counts.get("MISSING_FILE") or 0)
    processing_count = int(counts.get("PROCESSING") or 0)
    interrupted_count = int(counts.get("INTERRUPTED") or 0)
    pending_count = int(counts.get("REGISTERED") or 0) + interrupted_count

    hard_issues: list[dict[str, Any]] = []
    retry_issues: list[dict[str, Any]] = []

    if current_engine_version != "M1.6":
        hard_issues.append(
            {
                "code": "UNEXPECTED_ENGINE_VERSION",
                "engine_version": current_engine_version,
            }
        )
    if persistent_worker_running:
        hard_issues.append({"code": "PERSISTENT_WORKER_RUNNING"})
    if processing_count:
        hard_issues.append(
            {"code": "PROCESSING_PACKAGE_PRESENT", "rows": processing_count}
        )
    if storage_state.get("missing_m16_schema"):
        hard_issues.append(
            {
                "code": "M16_SCHEMA_INCOMPLETE",
                "missing": storage_state["missing_m16_schema"],
            }
        )
    for key, code in (
        ("goods_baseline_history_rows", "STORAGE_V2_GOODS_BASELINE_REGRESSION"),
        ("reconstructible_event_baseline_rows", "STORAGE_V2_EVENT_BASELINE_REGRESSION"),
        ("legacy_party_history_rows", "STORAGE_V2_PARTY_HISTORY_REGRESSION"),
    ):
        rows = int(storage_state.get(key) or 0)
        if rows:
            hard_issues.append({"code": code, "rows": rows})
    if storage_state.get("storage_v2_shadow_tables"):
        hard_issues.append(
            {
                "code": "STORAGE_V2_SHADOW_PRESENT",
                "tables": storage_state["storage_v2_shadow_tables"],
            }
        )
    if storage_state.get("pending_mutations"):
        hard_issues.append(
            {
                "code": "CLICKHOUSE_MUTATION_PENDING",
                "mutations": storage_state["pending_mutations"],
            }
        )
    stage_rows = int(storage_state.get("active_stage_rows") or 0)
    if stage_rows and processing_count == 0:
        hard_issues.append({"code": "ORPHAN_CN_STAGE_ROWS", "rows": stage_rows})

    if failed_count or missing_count:
        retry_issues.append(
            {
                "code": "FAILED_PACKAGE_RETRY_REQUIRED",
                "failed": failed_count,
                "missing_file": missing_count,
                "next_retry": package_state.get("next_retry"),
            }
        )

    if hard_issues:
        status = "BLOCKED"
        resume_mode = "NONE"
        safe_to_resume = False
        safe_to_resume_failed = False
    elif retry_issues:
        status = "RETRY_REQUIRED"
        resume_mode = "RESUME_FAILED"
        safe_to_resume = False
        safe_to_resume_failed = True
    elif pending_count:
        status = "READY"
        resume_mode = "NORMAL"
        safe_to_resume = True
        safe_to_resume_failed = False
    else:
        status = "COMPLETE"
        resume_mode = "NONE"
        safe_to_resume = False
        safe_to_resume_failed = False

    return {
        "readiness_version": READINESS_VERSION,
        "read_only": True,
        "status": status,
        "resume_mode": resume_mode,
        "safe_to_resume": safe_to_resume,
        "safe_to_resume_failed": safe_to_resume_failed,
        "engine_version": current_engine_version,
        "persistent_worker_running": persistent_worker_running,
        "hard_issues": hard_issues,
        "retry_issues": retry_issues,
        "packages": package_state,
        "storage_v2": storage_state,
    }


def build_readiness(*, persistent_worker_running: bool = False) -> dict[str, Any]:
    return evaluate_readiness(
        package_state=_package_state(),
        storage_state=_storage_state(),
        persistent_worker_running=persistent_worker_running,
        current_engine_version=engine_version(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only CN M1.6 replay readiness and resume diagnostic"
    )
    parser.add_argument(
        "--persistent-worker-running",
        action="store_true",
        help="Report that the persistent docker compose worker is currently running.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report = build_readiness(persistent_worker_running=args.persistent_worker_running)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    return 0 if report["status"] in {"READY", "COMPLETE", "RETRY_REQUIRED"} else 4


if __name__ == "__main__":
    raise SystemExit(main())
