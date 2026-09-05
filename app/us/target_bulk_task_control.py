from __future__ import annotations

from typing import Any

from app.db import postgres_conn
from app.us.target_bulk_tasks import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_NEEDS_OPERATOR,
    STATUS_PREPARE_QUEUED,
    STATUS_RUNNING,
    STATUS_RUN_QUEUED,
    TARGET_BULK_EXECUTION_LANE,
    TARGET_BULK_TASK_KIND,
)


_RESUMABLE = {STATUS_BLOCKED, STATUS_FAILED, STATUS_INTERRUPTED}


def fail_closed_recover_target_bulk_tasks() -> dict[str, int]:
    """Recover host-worker restart state without blind replay of an uncertain mutation."""
    report = {
        "read_only_requeued": 0,
        "mutation_blocked": 0,
        "stop_interrupted": 0,
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, payload
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND status = %s
                FOR UPDATE
                """,
                (TARGET_BULK_TASK_KIND, TARGET_BULK_EXECUTION_LANE, STATUS_RUNNING),
            )
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                payload = dict(row.get("payload") or {})
                if bool(payload.get("stop_requested")):
                    status = STATUS_INTERRUPTED
                    message = (
                        "Host worker restarted after STOP was requested; task remains stopped "
                        "and no child was automatically replayed."
                    )
                    report["stop_interrupted"] += 1
                elif payload.get("approved_plan_sha256"):
                    status = STATUS_BLOCKED
                    message = (
                        "Host worker restarted during an approved target mutation. "
                        "Automatic replay is forbidden; inspect the durable child journal and "
                        "RESUME explicitly."
                    )
                    report["mutation_blocked"] += 1
                else:
                    status = STATUS_PREPARE_QUEUED
                    message = (
                        "Host worker restarted during read-only plan preparation; queued for safe "
                        "read-only rebuild."
                    )
                    report["read_only_requeued"] += 1

                cur.execute(
                    """
                    UPDATE control.job_run
                    SET status = %s,
                        finished_at = CASE
                            WHEN %s IN (%s, %s) THEN now()
                            ELSE NULL
                        END,
                        error_message = %s
                    WHERE run_id = %s
                    """,
                    (
                        status,
                        status,
                        STATUS_BLOCKED,
                        STATUS_INTERRUPTED,
                        message,
                        row["run_id"],
                    ),
                )
        conn.commit()
    return report


def resumable_target_bulk_task() -> dict[str, Any] | None:
    """Return the newest frozen target task that must be resumed instead of superseded."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, job_type, trigger_type, status, started_at, finished_at,
                       payload, metrics, COALESCE(error_message, '') AS error_message
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND status = ANY(%s)
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (
                    TARGET_BULK_TASK_KIND,
                    TARGET_BULK_EXECUTION_LANE,
                    list(sorted(_RESUMABLE)),
                ),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def resume_target_bulk_task(*, run_id: str | None = None) -> dict[str, Any]:
    """Resume only from durable host-task state; never clear target journals or rows."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            if run_id:
                cur.execute(
                    """
                    SELECT run_id, status, payload, metrics
                    FROM control.job_run
                    WHERE run_id = %s
                      AND trigger_type = 'ADMIN_UI'
                      AND payload->>'task_kind' = %s
                      AND payload->>'execution_lane' = %s
                      AND payload->>'domain' = 'US_APPLICATION'
                    FOR UPDATE
                    """,
                    (run_id, TARGET_BULK_TASK_KIND, TARGET_BULK_EXECUTION_LANE),
                )
            else:
                cur.execute(
                    """
                    SELECT run_id, status, payload, metrics
                    FROM control.job_run
                    WHERE trigger_type = 'ADMIN_UI'
                      AND payload->>'task_kind' = %s
                      AND payload->>'execution_lane' = %s
                      AND payload->>'domain' = 'US_APPLICATION'
                      AND status = ANY(%s)
                    ORDER BY started_at DESC, run_id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (
                        TARGET_BULK_TASK_KIND,
                        TARGET_BULK_EXECUTION_LANE,
                        list(sorted(_RESUMABLE)),
                    ),
                )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return {"accepted": False, "task": None}
            task = dict(row)
            status = str(task.get("status") or "")
            if status not in _RESUMABLE:
                raise ValueError(f"US target bulk task is not resumable from status {status}")
            payload = dict(task.get("payload") or {})
            approved = str(payload.get("approved_plan_sha256") or "")
            prepared = str(payload.get("plan_sha256") or "")
            if approved:
                next_status = STATUS_RUN_QUEUED
                host_phase = "EXECUTE"
            elif prepared:
                next_status = STATUS_NEEDS_OPERATOR
                host_phase = "AWAITING_APPROVAL"
            else:
                next_status = STATUS_PREPARE_QUEUED
                host_phase = "PREPARE"

            cur.execute(
                """
                UPDATE control.job_run
                SET status = %s,
                    finished_at = NULL,
                    payload = payload || jsonb_build_object(
                        'host_phase', %s,
                        'stop_requested', false,
                        'resume_requested_at', now()
                    ),
                    error_message = NULL
                WHERE run_id = %s
                RETURNING run_id, job_type, trigger_type, status, started_at,
                          finished_at, payload, metrics, error_message
                """,
                (next_status, host_phase, task["run_id"]),
            )
            updated = dict(cur.fetchone())
        conn.commit()
    return {"accepted": True, "task": updated}
