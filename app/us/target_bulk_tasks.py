from __future__ import annotations

import json
from typing import Any

from app.db import postgres_conn


TARGET_BULK_TASK_VERSION = "US_APPLICATION_TARGET_BULK_TASK_V1"
TARGET_BULK_TASK_KIND = "US_APPLICATION_TARGET_BULK_CONTROL"
TARGET_BULK_EXECUTION_LANE = "WINDOWS_HOST_TARGET"
TARGET_BULK_DOMAIN = "US_APPLICATION"
TARGET_BULK_ACTION = "CONTINUE"
TARGET_BULK_EXPECTED_HISTORY_PARTS = 91
TARGET_BULK_START_SEQUENCE = 3
TARGET_BULK_SOURCE_COUNT = 310

STATUS_PREPARE_QUEUED = "HOST_PREPARE_QUEUED"
STATUS_NEEDS_OPERATOR = "NEEDS_OPERATOR"
STATUS_RUN_QUEUED = "HOST_RUN_QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_INTERRUPTED = "INTERRUPTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "FAILED"
STATUS_SUCCESS = "SUCCESS"

_ACTIVE_STATUSES = {
    STATUS_PREPARE_QUEUED,
    STATUS_NEEDS_OPERATOR,
    STATUS_RUN_QUEUED,
    STATUS_RUNNING,
}
_CLAIMABLE_STATUSES = {STATUS_PREPARE_QUEUED, STATUS_RUN_QUEUED}
_QUEUE_LOCK_NAME = "markorbit:us-application-target-bulk-task-queue"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _validate_bound(
    *,
    start_sequence: int,
    end_sequence: int | None,
    max_packages: int | None,
) -> tuple[int | None, int | None]:
    if start_sequence < TARGET_BULK_START_SEQUENCE or start_sequence > TARGET_BULK_SOURCE_COUNT:
        raise ValueError(
            f"start_sequence must be between {TARGET_BULK_START_SEQUENCE} "
            f"and {TARGET_BULK_SOURCE_COUNT}"
        )
    if bool(end_sequence is None) == bool(max_packages is None):
        raise ValueError("provide exactly one of end_sequence or max_packages")
    if end_sequence is not None:
        if end_sequence < start_sequence or end_sequence > TARGET_BULK_SOURCE_COUNT:
            raise ValueError("end_sequence is outside the accepted source corpus")
        return int(end_sequence), None
    assert max_packages is not None
    if max_packages < 1:
        raise ValueError("max_packages must be at least 1")
    if start_sequence + max_packages - 1 > TARGET_BULK_SOURCE_COUNT:
        raise ValueError("max_packages exceeds the accepted source corpus")
    return None, int(max_packages)


def queue_target_bulk_prepare(
    *,
    start_sequence: int = TARGET_BULK_START_SEQUENCE,
    end_sequence: int | None = None,
    max_packages: int | None = None,
) -> dict[str, Any]:
    """Queue a read-only host-side plan build. No production mutation is authorized."""
    end_sequence, max_packages = _validate_bound(
        start_sequence=start_sequence,
        end_sequence=end_sequence,
        max_packages=max_packages,
    )
    payload: dict[str, Any] = {
        "task_kind": TARGET_BULK_TASK_KIND,
        "task_version": TARGET_BULK_TASK_VERSION,
        "domain": TARGET_BULK_DOMAIN,
        "action": TARGET_BULK_ACTION,
        "execution_lane": TARGET_BULK_EXECUTION_LANE,
        "host_phase": "PREPARE",
        "expected_history_parts": TARGET_BULK_EXPECTED_HISTORY_PARTS,
        "start_sequence": int(start_sequence),
        "end_sequence": end_sequence,
        "max_packages": max_packages,
        "production_mutation_authorized": False,
        "stop_requested": False,
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_QUEUE_LOCK_NAME,))
            cur.execute(
                """
                SELECT run_id, job_type, status, started_at, payload, metrics
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND status = ANY(%s)
                ORDER BY started_at, run_id
                LIMIT 1
                """,
                (TARGET_BULK_TASK_KIND, list(sorted(_ACTIVE_STATUSES))),
            )
            existing = cur.fetchone()
            if existing:
                conn.commit()
                return {"accepted": False, "task": dict(existing)}

            cur.execute(
                """
                INSERT INTO control.job_run (
                    job_type, trigger_type, status, started_at, payload, metrics
                )
                VALUES (
                    'US_APPLICATION_ADMIN_CONTINUE',
                    'ADMIN_UI',
                    %s,
                    now(),
                    %s::jsonb,
                    '{}'::jsonb
                )
                RETURNING run_id, job_type, trigger_type, status, started_at, payload, metrics
                """,
                (STATUS_PREPARE_QUEUED, _json(payload)),
            )
            task = dict(cur.fetchone())
        conn.commit()
    return {"accepted": True, "task": task}


def active_target_bulk_task() -> dict[str, Any] | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, job_type, trigger_type, status, started_at, finished_at,
                       payload, metrics, COALESCE(error_message, '') AS error_message
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND payload->>'execution_lane' = %s
                  AND status = ANY(%s)
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (
                    TARGET_BULK_TASK_KIND,
                    TARGET_BULK_EXECUTION_LANE,
                    list(sorted(_ACTIVE_STATUSES)),
                ),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def approve_target_bulk_task(*, run_id: str, plan_sha256: str) -> dict[str, Any]:
    digest = plan_sha256.strip().lower()
    if len(digest) != 64:
        raise ValueError("plan_sha256 must be a 64-character SHA-256")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError("plan_sha256 must be hexadecimal") from exc

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_QUEUE_LOCK_NAME,))
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
            row = cur.fetchone()
            if not row:
                raise ValueError("US Application target bulk task was not found")
            task = dict(row)
            if str(task.get("status") or "") != STATUS_NEEDS_OPERATOR:
                raise ValueError("US Application target bulk task is not awaiting operator approval")
            metrics = dict(task.get("metrics") or {})
            prepared_sha = str(metrics.get("plan_sha256") or "").lower()
            if prepared_sha != digest:
                raise ValueError(
                    "operator approval plan SHA does not match the prepared immutable plan"
                )
            cur.execute(
                """
                UPDATE control.job_run
                SET status = %s,
                    finished_at = NULL,
                    payload = payload || jsonb_build_object(
                        'host_phase', 'EXECUTE',
                        'approved_plan_sha256', %s,
                        'production_mutation_authorized', true,
                        'approved_at', now(),
                        'stop_requested', false
                    ),
                    error_message = NULL
                WHERE run_id = %s
                RETURNING run_id, job_type, trigger_type, status, started_at,
                          payload, metrics, error_message
                """,
                (STATUS_RUN_QUEUED, digest, run_id),
            )
            updated = dict(cur.fetchone())
        conn.commit()
    return {"accepted": True, "task": updated}


def request_target_bulk_stop() -> dict[str, Any]:
    """Stop a prepared/queued task immediately or request package-boundary stop while running."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_QUEUE_LOCK_NAME,))
            cur.execute(
                """
                SELECT run_id, status, payload
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND status = ANY(%s)
                ORDER BY started_at, run_id
                LIMIT 1
                FOR UPDATE
                """,
                (
                    TARGET_BULK_TASK_KIND,
                    TARGET_BULK_EXECUTION_LANE,
                    list(sorted(_ACTIVE_STATUSES)),
                ),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return {"accepted": False, "task": None}
            task = dict(row)
            status = str(task.get("status") or "")
            if status == STATUS_RUNNING:
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET payload = payload || jsonb_build_object(
                            'stop_requested', true,
                            'stop_requested_at', now()
                        ),
                        error_message = 'Stop requested; host worker will stop at a safe package boundary.'
                    WHERE run_id = %s
                    RETURNING run_id, job_type, trigger_type, status, started_at,
                              finished_at, payload, metrics, error_message
                    """,
                    (task["run_id"],),
                )
            else:
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET status = %s,
                        finished_at = now(),
                        payload = payload || jsonb_build_object(
                            'stop_requested', true,
                            'stop_requested_at', now()
                        ),
                        error_message = 'Stopped before target bulk mutation started.'
                    WHERE run_id = %s
                    RETURNING run_id, job_type, trigger_type, status, started_at,
                              finished_at, payload, metrics, error_message
                    """,
                    (STATUS_INTERRUPTED, task["run_id"]),
                )
            updated = dict(cur.fetchone())
        conn.commit()
    return {"accepted": True, "task": updated}


def claim_next_target_bulk_task() -> dict[str, Any] | None:
    """Claim only Windows-host target tasks; the container worker never sees these statuses."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, job_type, trigger_type, status, started_at, payload, metrics
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND status = ANY(%s)
                ORDER BY started_at, run_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (
                    TARGET_BULK_TASK_KIND,
                    TARGET_BULK_EXECUTION_LANE,
                    list(sorted(_CLAIMABLE_STATUSES)),
                ),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            claimed = dict(row)
            claimed_from_status = str(claimed["status"])
            cur.execute(
                """
                UPDATE control.job_run
                SET status = %s,
                    finished_at = NULL,
                    payload = payload || jsonb_build_object(
                        'host_claimed_from_status', %s,
                        'host_claimed_at', now()
                    ),
                    error_message = NULL
                WHERE run_id = %s
                RETURNING run_id, job_type, trigger_type, status, started_at, payload, metrics
                """,
                (STATUS_RUNNING, claimed_from_status, claimed["run_id"]),
            )
            claimed = dict(cur.fetchone())
            claimed["claimed_from_status"] = claimed_from_status
        conn.commit()
    return claimed


def recover_interrupted_target_bulk_tasks() -> int:
    """Requeue host work after process restart; never erase durable plan/journal bindings."""
    recovered = 0
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
                    message = "Host worker restarted after stop was requested; task not requeued."
                elif payload.get("approved_plan_sha256"):
                    status = STATUS_RUN_QUEUED
                    message = "Host worker restarted during target bulk execution; queued for durable resume."
                    recovered += 1
                else:
                    status = STATUS_PREPARE_QUEUED
                    message = "Host worker restarted during plan preparation; queued for read-only rebuild."
                    recovered += 1
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET status = %s,
                        finished_at = CASE WHEN %s = %s THEN now() ELSE NULL END,
                        error_message = %s
                    WHERE run_id = %s
                    """,
                    (status, status, STATUS_INTERRUPTED, message, row["run_id"]),
                )
        conn.commit()
    return recovered


def target_bulk_stop_requested(run_id: str) -> bool:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload->>'stop_requested' AS stop_requested "
                "FROM control.job_run WHERE run_id = %s",
                (run_id,),
            )
            row = cur.fetchone()
    return bool(row and str(row.get("stop_requested") or "").lower() == "true")


def update_target_bulk_task(
    run_id: str,
    *,
    status: str | None = None,
    payload_patch: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    error_message: str | None = None,
    finish: bool = False,
) -> dict[str, Any]:
    payload_patch = dict(payload_patch or {})
    metrics = dict(metrics or {})
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET status = COALESCE(%s, status),
                    payload = payload || %s::jsonb,
                    metrics = metrics || %s::jsonb,
                    error_message = %s,
                    finished_at = CASE WHEN %s THEN now() ELSE finished_at END
                WHERE run_id = %s
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                RETURNING run_id, job_type, trigger_type, status, started_at,
                          finished_at, payload, metrics, error_message
                """,
                (
                    status,
                    _json(payload_patch),
                    _json(metrics),
                    error_message,
                    bool(finish),
                    run_id,
                    TARGET_BULK_TASK_KIND,
                    TARGET_BULK_EXECUTION_LANE,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("US Application target bulk task disappeared during update")
            updated = dict(row)
        conn.commit()
    return updated
