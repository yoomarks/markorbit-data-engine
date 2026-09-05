from __future__ import annotations

from typing import Any

from app.db import postgres_conn
from app.us.target_bulk_tasks import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PREPARE_QUEUED,
    STATUS_RUN_QUEUED,
    TARGET_BULK_EXECUTION_LANE,
)


_RESUMABLE = {STATUS_BLOCKED, STATUS_FAILED, STATUS_INTERRUPTED}


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
                      AND payload->>'execution_lane' = %s
                      AND payload->>'domain' = 'US_APPLICATION'
                    FOR UPDATE
                    """,
                    (run_id, TARGET_BULK_EXECUTION_LANE),
                )
            else:
                cur.execute(
                    """
                    SELECT run_id, status, payload, metrics
                    FROM control.job_run
                    WHERE trigger_type = 'ADMIN_UI'
                      AND payload->>'execution_lane' = %s
                      AND payload->>'domain' = 'US_APPLICATION'
                      AND status = ANY(%s)
                    ORDER BY started_at DESC, run_id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (TARGET_BULK_EXECUTION_LANE, list(sorted(_RESUMABLE))),
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
                raise ValueError(
                    "prepared US target bulk plan still requires explicit operator approval; "
                    "resume cannot substitute for approval"
                )
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
