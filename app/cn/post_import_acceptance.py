from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from app.cn.final_checkpoint import build_final_checkpoint
from app.cn.replay_readiness import build_readiness
from app.db import postgres_conn


POST_IMPORT_VERSION = "CN_M16_POST_IMPORT_ACCEPTANCE_V1"

_EXPECTED_PACKAGE_SQL = """
SELECT
    package_id,
    file_name,
    package_kind,
    partition_value,
    source_rank,
    status,
    processed_at,
    error_message
FROM control.source_package
WHERE jurisdiction = 'CN'
  AND file_name = %s
ORDER BY package_sequence
"""


def _package_rows(expected_file_name: str) -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_EXPECTED_PACKAGE_SQL, (expected_file_name,))
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def _normalized_package_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "package_id": row.get("package_id"),
            "file_name": row.get("file_name"),
            "package_kind": row.get("package_kind"),
            "partition_value": row.get("partition_value"),
            "source_rank": row.get("source_rank"),
            "status": row.get("status"),
            "processed_at": row.get("processed_at"),
            "error_message": row.get("error_message"),
        }
        for row in rows
    ]


def evaluate_post_import_acceptance(
    *,
    expected_file_name: str,
    package_rows: Sequence[Mapping[str, Any]],
    readiness: dict[str, Any],
    final_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    matches = _normalized_package_rows(package_rows)
    statuses = [str(row.get("status") or "") for row in matches]
    expected_package_success = any(status == "SUCCESS" for status in statuses)

    reasons: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not matches:
        reasons.append(
            {
                "code": "EXPECTED_PACKAGE_NOT_REGISTERED",
                "file_name": expected_file_name,
            }
        )
    elif not expected_package_success:
        reasons.append(
            {
                "code": "EXPECTED_PACKAGE_NOT_SUCCESS",
                "file_name": expected_file_name,
                "statuses": statuses,
            }
        )
    if len(matches) > 1:
        warnings.append(
            {
                "code": "EXPECTED_PACKAGE_MULTIPLE_ROWS",
                "file_name": expected_file_name,
                "rows": len(matches),
            }
        )

    readiness_status = str(readiness.get("status") or "UNKNOWN")
    if readiness_status == "BLOCKED":
        reasons.extend(readiness.get("hard_issues") or [])

    if reasons:
        status = "BLOCKED"
        next_action = {"mode": "STOP_AND_REVIEW", "command": None}
    elif readiness_status == "RETRY_REQUIRED":
        status = "RETRY_REQUIRED"
        next_action = {
            "mode": "RESUME_FAILED",
            "command": (
                "powershell.exe -ExecutionPolicy Bypass "
                "-File .\\scripts\\replay-cn-full.ps1 -ResumeFailed"
            ),
        }
    elif readiness_status == "READY":
        status = "READY_TO_CONTINUE"
        next_action = {
            "mode": "NORMAL",
            "command": (
                "powershell.exe -ExecutionPolicy Bypass "
                "-File .\\scripts\\replay-cn-full.ps1"
            ),
        }
    elif readiness_status == "COMPLETE":
        checkpoint_status = str((final_checkpoint or {}).get("status") or "UNKNOWN")
        if checkpoint_status in {"PASS", "PASS_WITH_WARNINGS"}:
            status = checkpoint_status
            next_action = {"mode": "CN_REPLAY_ACCEPTED", "command": None}
        else:
            status = "FINAL_CHECKPOINT_FAILED"
            reasons.extend((final_checkpoint or {}).get("reasons") or [])
            if not reasons:
                reasons.append(
                    {
                        "code": "FINAL_CHECKPOINT_NOT_PASSING",
                        "checkpoint_status": checkpoint_status,
                    }
                )
            next_action = {"mode": "STOP_AND_REVIEW", "command": None}
    else:
        status = "BLOCKED"
        reasons.append(
            {
                "code": "UNEXPECTED_REPLAY_READINESS_STATUS",
                "readiness_status": readiness_status,
            }
        )
        next_action = {"mode": "STOP_AND_REVIEW", "command": None}

    return {
        "post_import_version": POST_IMPORT_VERSION,
        "read_only": True,
        "expected_file_name": expected_file_name,
        "status": status,
        "expected_package_success": expected_package_success,
        "expected_package_rows": matches,
        "warnings": warnings,
        "reasons": reasons,
        "readiness_status": readiness_status,
        "readiness": readiness,
        "final_checkpoint_executed": final_checkpoint is not None,
        "final_checkpoint": final_checkpoint,
        "next_action": next_action,
    }


def build_post_import_acceptance(
    *,
    expected_file_name: str,
    persistent_worker_running: bool = False,
    package_rows_builder: Callable[[str], Sequence[Mapping[str, Any]]] = _package_rows,
    readiness_builder: Callable[..., dict[str, Any]] = build_readiness,
    final_checkpoint_builder: Callable[..., dict[str, Any]] = build_final_checkpoint,
) -> dict[str, Any]:
    """Audit one completed CN package and choose the safe next replay action.

    This function is database read-only. It verifies that the named package reached
    SUCCESS, then reuses the authoritative M1.6 replay-readiness gate. The expensive
    final checkpoint executes only when the whole CN replay is COMPLETE.
    """
    rows = package_rows_builder(expected_file_name)
    readiness = readiness_builder(
        persistent_worker_running=persistent_worker_running
    )

    expected_package_success = any(
        str(row.get("status") or "") == "SUCCESS" for row in rows
    )
    final_checkpoint = None
    if expected_package_success and readiness.get("status") == "COMPLETE":
        final_checkpoint = final_checkpoint_builder(
            persistent_worker_running=persistent_worker_running,
            readiness_builder=lambda **_: readiness,
        )

    return evaluate_post_import_acceptance(
        expected_file_name=expected_file_name,
        package_rows=rows,
        readiness=readiness,
        final_checkpoint=final_checkpoint,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only CN package post-import acceptance and next-action gate"
    )
    parser.add_argument("--expected-file-name", required=True)
    parser.add_argument("--persistent-worker-running", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = build_post_import_acceptance(
        expected_file_name=args.expected_file_name,
        persistent_worker_running=args.persistent_worker_running,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    return 0 if report["status"] in {
        "PASS",
        "PASS_WITH_WARNINGS",
        "READY_TO_CONTINUE",
        "RETRY_REQUIRED",
    } else 4


if __name__ == "__main__":
    raise SystemExit(main())
