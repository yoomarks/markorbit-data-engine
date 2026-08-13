from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
from typing import Any, Iterator

from app.cn.final_checkpoint import build_final_checkpoint
from app.cn.guarded_run_once import build_execution_guard
from app.config import get_settings
from app.db import postgres_conn
from app.jobs import ingest_pending_cn, scan_and_ingest_cn
from app.repository import finish_job_run
from app.storage_headroom import (
    DEFAULT_MIN_FREE_GIB,
    DEFAULT_MIN_FREE_PERCENT,
    DEFAULT_RESERVE_GIB,
    GIB,
    build_headroom_report,
)
from app.us.jobs import ingest_pending_us, scan_and_ingest_us
from app.us.migrations import ensure_us_m1_schema
from app.us_assignment.jobs import run_assignment_once
from app.us_assignment.migrations import ensure_assignment_schema
from app.us_assignment.transition_gate import build_transition_gate as build_assignment_gate
from app.us_ttab.jobs import run_ttab_once
from app.us_ttab.migrations import ensure_ttab_schema
from app.us_ttab.transition_gate import build_transition_gate as build_ttab_gate


ADMIN_TASK_KIND = "DOMAIN_CONTROL"
ADMIN_TRIGGER = "ADMIN_UI"
SUPPORTED_DOMAINS = {"CN", "US_APPLICATION", "US_ASSIGNMENT", "US_TTAB"}
SUPPORTED_ACTIONS = {"RUN", "RETRY"}
_MUTATION_LOCK_NAME = "markorbit:admin-domain-mutation"
_QUEUE_LOCK_NAME = "markorbit:admin-domain-task-queue"


class DomainTaskBlocked(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _job_type(domain: str, action: str) -> str:
    return f"{domain}_ADMIN_{action}"


def queue_admin_domain_task(
    *,
    domain: str,
    action: str,
    expected_history_parts: int = 0,
) -> dict[str, Any]:
    domain = domain.strip().upper()
    action = action.strip().upper()
    if domain not in SUPPORTED_DOMAINS:
        raise ValueError(f"Unsupported domain: {domain}")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported action: {action}")
    if domain != "CN" and expected_history_parts < 1:
        raise ValueError("expected_history_parts is required for US domain tasks")
    if expected_history_parts > 9999:
        raise ValueError("expected_history_parts must be <= 9999")

    payload = {
        "task_kind": ADMIN_TASK_KIND,
        "domain": domain,
        "action": action,
        "expected_history_parts": int(expected_history_parts),
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_QUEUE_LOCK_NAME,))
            cur.execute(
                """
                SELECT run_id, job_type, status, started_at, payload
                FROM control.job_run
                WHERE trigger_type = %s
                  AND payload->>'task_kind' = %s
                  AND payload->>'domain' = %s
                  AND status IN ('QUEUED', 'RUNNING')
                ORDER BY started_at
                LIMIT 1
                """,
                (ADMIN_TRIGGER, ADMIN_TASK_KIND, domain),
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
                VALUES (%s, %s, 'QUEUED', now(), %s::jsonb, '{}'::jsonb)
                RETURNING run_id, job_type, trigger_type, status, started_at, payload
                """,
                (_job_type(domain, action), ADMIN_TRIGGER, _json(payload)),
            )
            task = dict(cur.fetchone())
        conn.commit()
    return {"accepted": True, "task": task}


def recover_interrupted_admin_domain_tasks() -> int:
    """Requeue only Admin UI wrapper tasks left RUNNING by a worker restart."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET status = 'QUEUED',
                    finished_at = NULL,
                    error_message = 'Worker restarted before admin task completion; requeued.'
                WHERE trigger_type = %s
                  AND payload->>'task_kind' = %s
                  AND status = 'RUNNING'
                """,
                (ADMIN_TRIGGER, ADMIN_TASK_KIND),
            )
            recovered = int(cur.rowcount or 0)
        conn.commit()
    return recovered


def claim_next_admin_domain_task() -> dict[str, Any] | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH next_task AS (
                    SELECT run_id
                    FROM control.job_run
                    WHERE trigger_type = %s
                      AND payload->>'task_kind' = %s
                      AND status = 'QUEUED'
                    ORDER BY started_at, run_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE control.job_run AS j
                SET status = 'RUNNING',
                    started_at = now(),
                    finished_at = NULL,
                    metrics = '{}'::jsonb,
                    error_message = NULL
                FROM next_task
                WHERE j.run_id = next_task.run_id
                RETURNING j.run_id, j.job_type, j.trigger_type, j.status,
                          j.started_at, j.payload
                """,
                (ADMIN_TRIGGER, ADMIN_TASK_KIND),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


@contextmanager
def engine_mutation_guard() -> Iterator[bool]:
    """Serialize worker-driven domain mutations across all trademark domains."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                (_MUTATION_LOCK_NAME,),
            )
            acquired = bool(cur.fetchone()["acquired"])
        try:
            yield acquired
        finally:
            if acquired:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        (_MUTATION_LOCK_NAME,),
                    )
                conn.commit()


def _assert_storage_headroom() -> dict[str, Any]:
    clickhouse = build_headroom_report()
    if not clickhouse.get("safe_to_mutate"):
        raise DomainTaskBlocked(
            f"ClickHouse storage headroom gate blocked mutation: {clickhouse.get('reason_codes')}"
        )

    raw_root = get_settings().raw_data_root
    try:
        usage = shutil.disk_usage(raw_root)
    except OSError as exc:
        raise DomainTaskBlocked(f"Unable to inspect raw-data host volume: {exc}") from exc

    absolute_required = int(DEFAULT_MIN_FREE_GIB * GIB)
    percent_required = int(usage.total * DEFAULT_MIN_FREE_PERCENT / 100.0)
    reserve = int(DEFAULT_RESERVE_GIB * GIB)
    required = max(absolute_required, percent_required) + reserve
    if usage.free < required:
        raise DomainTaskBlocked(
            "Host raw-data volume free space is below policy: "
            f"free={usage.free}, required={required}"
        )
    return {
        "clickhouse": clickhouse,
        "host": {
            "path": str(raw_root),
            "total_bytes": int(usage.total),
            "free_bytes": int(usage.free),
            "required_free_bytes": required,
        },
    }


def _assert_cn_run_gate() -> dict[str, Any]:
    guard = build_execution_guard()
    if not guard.get("allowed") or guard.get("mode") != "REGISTERED_REPLAY_CONTINUATION":
        raise DomainTaskBlocked(
            "CN Admin run is only allowed for registered replay continuation. "
            f"Current mode: {guard.get('mode')}"
        )
    return guard


def _assert_cn_accepted() -> dict[str, Any]:
    checkpoint = build_final_checkpoint(persistent_worker_running=False)
    if checkpoint.get("status") not in {"PASS", "PASS_WITH_WARNINGS"} or not checkpoint.get(
        "ready_for_next_domain"
    ):
        raise DomainTaskBlocked(
            f"US Application is blocked by CN final checkpoint: {checkpoint.get('status')}"
        )
    return checkpoint


def _assert_assignment_unlocked(raw_root, expected_history_parts: int) -> dict[str, Any]:
    report = build_assignment_gate(
        raw_root,
        expected_history_parts=expected_history_parts,
        persistent_worker_running=False,
    )
    if not report.get("ready_for_assignment_phase"):
        raise DomainTaskBlocked(
            f"US Assignment transition gate blocked mutation: {report.get('status')}"
        )
    return report


def _assert_ttab_unlocked(raw_root, expected_history_parts: int) -> dict[str, Any]:
    report = build_ttab_gate(
        raw_root,
        expected_history_parts=expected_history_parts,
        persistent_worker_running=False,
    )
    if not report.get("ready_for_ttab_phase"):
        raise DomainTaskBlocked(
            f"US TTAB transition gate blocked mutation: {report.get('status')}"
        )
    return report


def _check_ingest_result(result: dict[str, Any]) -> None:
    ingest = result.get("ingest") if isinstance(result.get("ingest"), dict) else result
    if ingest.get("busy") or result.get("status") == "BUSY":
        raise DomainTaskBlocked("The target ingestion domain is already busy")
    if int(ingest.get("failed") or 0) > 0:
        raise RuntimeError(f"Domain ingestion reported a failed package: {result}")
    if result.get("status") == "MISSING_FILE":
        raise RuntimeError(f"Domain retry could not locate its source package: {result}")


def execute_admin_domain_task(task: dict[str, Any]) -> dict[str, Any]:
    payload = dict(task.get("payload") or {})
    domain = str(payload.get("domain") or "").upper()
    action = str(payload.get("action") or "").upper()
    expected_history_parts = int(payload.get("expected_history_parts") or 0)
    raw_root = get_settings().raw_data_root

    storage = _assert_storage_headroom()
    gate: dict[str, Any] = {}

    if domain == "CN":
        if action == "RUN":
            gate = _assert_cn_run_gate()
            result = scan_and_ingest_cn(trigger_type="ADMIN_UI_GUARDED")
        else:
            result = ingest_pending_cn(
                trigger_type="ADMIN_UI_RETRY",
                include_failed=True,
                limit=1,
            )
    elif domain == "US_APPLICATION":
        gate = _assert_cn_accepted()
        ensure_us_m1_schema()
        if action == "RUN":
            result = scan_and_ingest_us(trigger_type="ADMIN_UI_US")
        else:
            result = ingest_pending_us(
                trigger_type="ADMIN_UI_US_RETRY",
                include_failed=True,
                limit=1,
            )
    elif domain == "US_ASSIGNMENT":
        gate = _assert_assignment_unlocked(raw_root, expected_history_parts)
        ensure_assignment_schema()
        result = run_assignment_once(raw_root, retry=action == "RETRY")
    elif domain == "US_TTAB":
        gate = _assert_ttab_unlocked(raw_root, expected_history_parts)
        ensure_ttab_schema()
        result = run_ttab_once(raw_root, retry=action == "RETRY")
    else:
        raise ValueError(f"Unsupported queued domain: {domain}")

    if not isinstance(result, dict):
        result = {"result": result}
    _check_ingest_result(result)
    return {
        "domain": domain,
        "action": action,
        "expected_history_parts": expected_history_parts,
        "gate_status": gate.get("status") or gate.get("mode") or "INTERNAL",
        "storage": storage,
        "result": result,
    }


def finish_admin_domain_task(task: dict[str, Any]) -> None:
    run_id = str(task["run_id"])
    try:
        metrics = execute_admin_domain_task(task)
    except DomainTaskBlocked as exc:
        finish_job_run(run_id, "BLOCKED", error_message=str(exc))
    except Exception as exc:
        finish_job_run(run_id, "FAILED", error_message=f"{type(exc).__name__}: {exc}")
    else:
        finish_job_run(run_id, "SUCCESS", metrics=metrics)
