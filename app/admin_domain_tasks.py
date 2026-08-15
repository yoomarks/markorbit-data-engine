from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from typing import Any, Iterator

from app.cn import full_replay
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
from app.us.application_transition_gate import build_transition_gate as build_application_gate
from app.us.jobs import ingest_pending_us, scan_and_ingest_us
from app.us.migrations import ensure_us_m1_schema
from app.us.replay_executor import execute_replay as execute_us_replay
from app.us_assignment.corpus_replay import execute_replay as execute_assignment_replay
from app.us_assignment.jobs import run_assignment_once
from app.us_assignment.migrations import ensure_assignment_schema
from app.us_assignment.transition_gate import build_transition_gate as build_assignment_gate
from app.us_ttab.corpus_replay import execute_replay as execute_ttab_replay
from app.us_ttab.jobs import run_ttab_once
from app.us_ttab.migrations import ensure_ttab_schema
from app.us_ttab.transition_gate import build_transition_gate as build_ttab_gate


ADMIN_TASK_KIND = "DOMAIN_CONTROL"
ADMIN_TRIGGER = "ADMIN_UI"
SUPPORTED_DOMAINS = {"CN", "US_APPLICATION", "US_ASSIGNMENT", "US_TTAB"}
SUPPORTED_ACTIONS = {"RUN", "RETRY", "CONTINUE"}
_CONTINUOUS_DOMAINS = {"CN", "US_APPLICATION", "US_ASSIGNMENT", "US_TTAB"}
_MUTATION_LOCK_NAME = "markorbit:admin-domain-mutation"
_QUEUE_LOCK_NAME = "markorbit:admin-domain-task-queue"


class DomainTaskBlocked(RuntimeError):
    pass


class DomainTaskInterrupted(RuntimeError):
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
    if action == "CONTINUE" and domain not in _CONTINUOUS_DOMAINS:
        raise ValueError("CONTINUE is only supported for trademark replay domains")
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


def request_admin_domain_stop(*, domain: str) -> dict[str, Any]:
    """Request a safe package-boundary stop for an active continuous replay."""
    domain = domain.strip().upper()
    if domain not in _CONTINUOUS_DOMAINS:
        raise ValueError("STOP is only supported for trademark continuous replay domains")

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
                  AND payload->>'action' = 'CONTINUE'
                  AND status IN ('QUEUED', 'RUNNING')
                ORDER BY started_at
                LIMIT 1
                FOR UPDATE
                """,
                (ADMIN_TRIGGER, ADMIN_TASK_KIND, domain),
            )
            task = cur.fetchone()
            if not task:
                conn.commit()
                return {"accepted": False, "task": None}

            if str(task["status"]).upper() == "QUEUED":
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET status = 'INTERRUPTED',
                        finished_at = now(),
                        payload = payload || jsonb_build_object(
                            'stop_requested', true,
                            'stop_requested_at', now()
                        ),
                        error_message = 'Stopped before continuous replay started.'
                    WHERE run_id = %s
                    RETURNING run_id, job_type, trigger_type, status,
                              started_at, finished_at, payload, error_message
                    """,
                    (task["run_id"],),
                )
            else:
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET payload = payload || jsonb_build_object(
                            'stop_requested', true,
                            'stop_requested_at', now()
                        ),
                        error_message = 'Stop requested; waiting for the current package boundary.'
                    WHERE run_id = %s
                    RETURNING run_id, job_type, trigger_type, status,
                              started_at, finished_at, payload, error_message
                    """,
                    (task["run_id"],),
                )
            updated = dict(cur.fetchone())
        conn.commit()
    return {"accepted": True, "task": updated}


def recover_interrupted_admin_domain_tasks() -> int:
    """Requeue interrupted wrappers unless a cooperative stop was already requested."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET status = 'INTERRUPTED',
                    finished_at = now(),
                    error_message = 'Worker restarted after stop was requested; task not requeued.'
                WHERE trigger_type = %s
                  AND payload->>'task_kind' = %s
                  AND status = 'RUNNING'
                  AND payload->>'stop_requested' = 'true'
                """,
                (ADMIN_TRIGGER, ADMIN_TASK_KIND),
            )
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


def _build_us_application_gate(
    raw_root, expected_history_parts: int, *, verify_source_files: bool = False
) -> dict[str, Any]:
    return build_application_gate(
        raw_root,
        expected_history_parts=expected_history_parts,
        verify_source_files=verify_source_files,
        persistent_worker_running=False,
    )


def _assert_us_application_replay_unlocked(
    raw_root, expected_history_parts: int
) -> dict[str, Any]:
    report = _build_us_application_gate(raw_root, expected_history_parts)
    if report.get("status") == "US_APPLICATION_ALREADY_ACCEPTED":
        return report
    if not report.get("safe_to_start_us_replay"):
        raise DomainTaskBlocked(
            f"US Application replay transition gate blocked mutation: {report.get('status')}"
        )
    return report


def _assert_us_application_accepted(raw_root, expected_history_parts: int) -> dict[str, Any]:
    report = _build_us_application_gate(
        raw_root,
        expected_history_parts,
        verify_source_files=True,
    )
    if report.get("status") != "US_APPLICATION_ALREADY_ACCEPTED" or not report.get(
        "ready_for_us_application"
    ):
        raise DomainTaskBlocked(
            f"US Application acceptance gate did not pass: {report.get('status')}"
        )
    return report


def _build_assignment_gate(
    raw_root,
    expected_history_parts: int,
    *,
    verify_us_source_files: bool = False,
    verify_assignment_sources: bool = False,
) -> dict[str, Any]:
    return build_assignment_gate(
        raw_root,
        expected_history_parts=expected_history_parts,
        verify_us_source_files=verify_us_source_files,
        verify_assignment_sources=verify_assignment_sources,
        persistent_worker_running=False,
    )


def _assert_assignment_unlocked(raw_root, expected_history_parts: int) -> dict[str, Any]:
    report = _build_assignment_gate(raw_root, expected_history_parts)
    if not report.get("ready_for_assignment_phase"):
        raise DomainTaskBlocked(
            f"US Assignment transition gate blocked mutation: {report.get('status')}"
        )
    return report


def _assert_assignment_accepted(raw_root, expected_history_parts: int) -> dict[str, Any]:
    report = _build_assignment_gate(
        raw_root,
        expected_history_parts,
        verify_us_source_files=True,
        verify_assignment_sources=True,
    )
    if (
        report.get("status") != "ASSIGNMENT_ACCEPTED"
        or not report.get("ready_for_assignment_phase")
        or not report.get("assignment_ready")
    ):
        raise DomainTaskBlocked(
            f"US Assignment acceptance gate did not pass: {report.get('status')}"
        )
    return report


def _build_ttab_gate(
    raw_root,
    expected_history_parts: int,
    *,
    verify_us_source_files: bool = False,
    verify_assignment_sources: bool = False,
    verify_ttab_sources: bool = False,
) -> dict[str, Any]:
    return build_ttab_gate(
        raw_root,
        expected_history_parts=expected_history_parts,
        verify_us_source_files=verify_us_source_files,
        verify_assignment_sources=verify_assignment_sources,
        verify_ttab_sources=verify_ttab_sources,
        persistent_worker_running=False,
    )


def _assert_ttab_unlocked(raw_root, expected_history_parts: int) -> dict[str, Any]:
    report = _build_ttab_gate(raw_root, expected_history_parts)
    if not report.get("ready_for_ttab_phase"):
        raise DomainTaskBlocked(
            f"US TTAB transition gate blocked mutation: {report.get('status')}"
        )
    return report


def _assert_ttab_accepted(raw_root, expected_history_parts: int) -> dict[str, Any]:
    report = _build_ttab_gate(
        raw_root,
        expected_history_parts,
        verify_us_source_files=True,
        verify_assignment_sources=True,
        verify_ttab_sources=True,
    )
    if (
        report.get("status") != "TTAB_ACCEPTED"
        or not report.get("ready_for_ttab_phase")
        or not report.get("ttab_ready")
    ):
        raise DomainTaskBlocked(
            f"US TTAB acceptance gate did not pass: {report.get('status')}"
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


def _continuation_stop_requested(run_id: str) -> bool:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload->>'stop_requested' AS stop_requested "
                "FROM control.job_run WHERE run_id = %s",
                (run_id,),
            )
            row = cur.fetchone()
    return bool(row and str(row.get("stop_requested") or "").lower() == "true")


def _assert_continuation_not_stopped(run_id: str, domain: str) -> None:
    if _continuation_stop_requested(run_id):
        raise DomainTaskInterrupted(
            f"{domain} continuous replay stopped by Admin request at a package boundary."
        )


def _run_cn_continuation(run_id: str) -> dict[str, Any]:
    def before_package(_phase: str) -> None:
        _assert_continuation_not_stopped(run_id, "CN")
        _assert_storage_headroom()

    code, summary = full_replay.run_full_replay(
        resume_failed=True,
        trigger_type="ADMIN_UI_CN_CONTINUE",
        emit=lambda _event: None,
        before_package=before_package,
        allow_clean_start=False,
    )
    if code in {3, 4}:
        raise DomainTaskBlocked(f"CN continuous replay stopped safely: {summary}")
    if code != 0:
        raise RuntimeError(f"CN continuous replay failed: {summary}")
    return {
        "runner_code": code,
        "runner_version": full_replay.RUNNER_VERSION,
        "summary": summary,
    }


def _checkpoint_result(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(checkpoint.get("status") or "UNKNOWN"),
        "ready_for_next_domain": bool(checkpoint.get("ready_for_next_domain")),
        "reasons": list(checkpoint.get("reasons") or []),
        "summary": dict(checkpoint.get("summary") or {}),
    }


def _us_application_gate_result(report: dict[str, Any]) -> dict[str, Any]:
    pipeline = report.get("us_pipeline") if isinstance(report.get("us_pipeline"), dict) else {}
    return {
        "status": str(report.get("status") or "UNKNOWN"),
        "ready_for_us_application": bool(report.get("ready_for_us_application")),
        "safe_to_start_us_replay": bool(report.get("safe_to_start_us_replay")),
        "reason_codes": list(report.get("reason_codes") or []),
        "us_pipeline_state": str(report.get("us_pipeline_state") or pipeline.get("state") or ""),
    }


def _assignment_gate_result(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(report.get("status") or "UNKNOWN"),
        "ready_for_assignment_phase": bool(report.get("ready_for_assignment_phase")),
        "assignment_ready": bool(report.get("assignment_ready")),
        "assignment_state": str(report.get("assignment_state") or ""),
        "reason_codes": list(report.get("reason_codes") or []),
        "legal_ownership_conclusion": False,
    }


def _ttab_gate_result(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(report.get("status") or "UNKNOWN"),
        "ready_for_ttab_phase": bool(report.get("ready_for_ttab_phase")),
        "ttab_ready": bool(report.get("ttab_ready")),
        "ttab_state": str(report.get("ttab_state") or ""),
        "reason_codes": list(report.get("reason_codes") or []),
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def _assignment_manifest_path(raw_root) -> Path:
    return Path(raw_root) / "manifests" / "us_assignment" / "corpus.json"


def _ttab_manifest_path(raw_root) -> Path:
    return Path(raw_root) / "manifests" / "us_ttab" / "corpus.json"


def _run_us_application_continuation(
    raw_root, expected_history_parts: int, run_id: str
) -> dict[str, Any]:
    def before_package(_step: dict[str, Any]) -> None:
        _assert_continuation_not_stopped(run_id, "US_APPLICATION")
        _assert_storage_headroom()

    replay = execute_us_replay(
        raw_root,
        expected_history_parts=expected_history_parts,
        max_packages=None,
        trigger_type="ADMIN_UI_US_CONTINUE",
        before_package=before_package,
    )
    status = str(replay.get("status") or "UNKNOWN")
    if status == "BUSY":
        raise DomainTaskBlocked("US Application deterministic replay is already busy")
    if status == "BLOCKED":
        blockers = (replay.get("final_plan") or {}).get("blockers") or []
        raise DomainTaskBlocked(f"US Application deterministic replay blocked: {blockers}")
    if status == "FAILED":
        raise RuntimeError(f"US Application deterministic replay failed: {replay.get('error')}")
    if status != "COMPLETE":
        raise RuntimeError(f"Unexpected US Application replay status: {status}")
    final_plan = replay.get("final_plan") if isinstance(replay.get("final_plan"), dict) else {}
    return {
        "status": status,
        "executor_version": replay.get("executor_version"),
        "processed_count": int(replay.get("processed_count") or 0),
        "remaining_count": int(final_plan.get("remaining_count") or 0),
        "source_preflight_runs": int(replay.get("source_preflight_runs") or 0),
    }


def _run_assignment_continuation(
    raw_root, expected_history_parts: int, run_id: str
) -> dict[str, Any]:
    def before_package(_action: dict[str, Any]) -> None:
        _assert_continuation_not_stopped(run_id, "US_ASSIGNMENT")
        _assert_storage_headroom()

    replay = execute_assignment_replay(
        _assignment_manifest_path(raw_root),
        Path(raw_root),
        apply=True,
        all_packages=True,
        resume_failed=True,
        before_package=before_package,
    )
    status = str(replay.get("status") or "UNKNOWN")
    if status in {"BUSY", "BLOCKED", "RETRY_REQUIRED"}:
        blockers = (replay.get("final_plan") or {}).get("blockers") or replay.get("blockers") or []
        raise DomainTaskBlocked(f"US Assignment deterministic replay blocked: {status}: {blockers}")
    if status == "FAILED":
        raise RuntimeError(f"US Assignment deterministic replay failed: {replay.get('error')}")
    if status != "COMPLETE":
        raise RuntimeError(f"Unexpected US Assignment replay status: {status}")
    final_plan = replay.get("final_plan") if isinstance(replay.get("final_plan"), dict) else {}
    return {
        "status": status,
        "replay_version": replay.get("replay_version"),
        "processed_count": int(replay.get("processed_count") or 0),
        "remaining_count": int(final_plan.get("remaining_count") or 0),
        "source_preflight_runs": int(replay.get("source_preflight_runs") or 0),
        "legal_ownership_conclusion": False,
    }


def _run_ttab_continuation(
    raw_root, expected_history_parts: int, run_id: str
) -> dict[str, Any]:
    def before_package(_action: dict[str, Any]) -> None:
        _assert_continuation_not_stopped(run_id, "US_TTAB")
        _assert_storage_headroom()

    replay = execute_ttab_replay(
        _ttab_manifest_path(raw_root),
        Path(raw_root),
        apply=True,
        all_packages=True,
        resume_failed=True,
        before_package=before_package,
    )
    status = str(replay.get("status") or "UNKNOWN")
    if status in {"BUSY", "BLOCKED", "RETRY_REQUIRED"}:
        blockers = (replay.get("final_plan") or {}).get("blockers") or replay.get("blockers") or []
        raise DomainTaskBlocked(f"US TTAB deterministic replay blocked: {status}: {blockers}")
    if status == "FAILED":
        raise RuntimeError(f"US TTAB deterministic replay failed: {replay.get('error')}")
    if status != "COMPLETE":
        raise RuntimeError(f"Unexpected US TTAB replay status: {status}")
    final_plan = replay.get("final_plan") if isinstance(replay.get("final_plan"), dict) else {}
    return {
        "status": status,
        "replay_version": replay.get("replay_version"),
        "processed_count": int(replay.get("processed_count") or 0),
        "remaining_count": int(final_plan.get("remaining_count") or 0),
        "source_preflight_runs": int(replay.get("source_preflight_runs") or 0),
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


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
        elif action == "RETRY":
            result = ingest_pending_cn(
                trigger_type="ADMIN_UI_RETRY",
                include_failed=True,
                limit=1,
            )
        elif action == "CONTINUE":
            result = _run_cn_continuation(str(task["run_id"]))
            checkpoint = _assert_cn_accepted()
            result["final_checkpoint"] = _checkpoint_result(checkpoint)
            gate = {
                "status": checkpoint.get("status") or "UNKNOWN",
                "ready_for_next_domain": bool(checkpoint.get("ready_for_next_domain")),
            }
        else:
            raise ValueError(f"Unsupported CN action: {action}")
    elif domain == "US_APPLICATION":
        gate = _assert_cn_accepted()
        ensure_us_m1_schema()
        if action == "RUN":
            result = scan_and_ingest_us(trigger_type="ADMIN_UI_US")
        elif action == "RETRY":
            result = ingest_pending_us(
                trigger_type="ADMIN_UI_US_RETRY",
                include_failed=True,
                limit=1,
            )
        elif action == "CONTINUE":
            start_gate = _assert_us_application_replay_unlocked(
                raw_root, expected_history_parts
            )
            if start_gate.get("status") == "US_APPLICATION_ALREADY_ACCEPTED":
                result = {
                    "status": "COMPLETE",
                    "processed_count": 0,
                    "remaining_count": 0,
                    "already_accepted": True,
                }
            else:
                result = _run_us_application_continuation(
                    raw_root,
                    expected_history_parts,
                    str(task.get("run_id") or ""),
                )
            final_gate = _assert_us_application_accepted(
                raw_root, expected_history_parts
            )
            result["final_application_gate"] = _us_application_gate_result(final_gate)
            gate = final_gate
        else:
            raise ValueError(f"Unsupported US Application action: {action}")
    elif domain == "US_ASSIGNMENT":
        gate = _assert_assignment_unlocked(raw_root, expected_history_parts)
        ensure_assignment_schema()
        if action == "RUN":
            result = run_assignment_once(raw_root, retry=False)
        elif action == "RETRY":
            result = run_assignment_once(raw_root, retry=True)
        elif action == "CONTINUE":
            if gate.get("status") == "ASSIGNMENT_ACCEPTED" and gate.get("assignment_ready"):
                result = {
                    "status": "COMPLETE",
                    "processed_count": 0,
                    "remaining_count": 0,
                    "already_accepted": True,
                    "legal_ownership_conclusion": False,
                }
            else:
                result = _run_assignment_continuation(
                    raw_root,
                    expected_history_parts,
                    str(task.get("run_id") or ""),
                )
            final_gate = _assert_assignment_accepted(raw_root, expected_history_parts)
            result["final_assignment_gate"] = _assignment_gate_result(final_gate)
            gate = final_gate
        else:
            raise ValueError(f"Unsupported US Assignment action: {action}")
    elif domain == "US_TTAB":
        gate = _assert_ttab_unlocked(raw_root, expected_history_parts)
        ensure_ttab_schema()
        if action == "RUN":
            result = run_ttab_once(raw_root, retry=False)
        elif action == "RETRY":
            result = run_ttab_once(raw_root, retry=True)
        elif action == "CONTINUE":
            if gate.get("status") == "TTAB_ACCEPTED" and gate.get("ttab_ready"):
                result = {
                    "status": "COMPLETE",
                    "processed_count": 0,
                    "remaining_count": 0,
                    "already_accepted": True,
                    "deadline_validity_inference": False,
                    "legal_outcome_conclusion": False,
                    "substantive_rights_conclusion": False,
                }
            else:
                result = _run_ttab_continuation(
                    raw_root,
                    expected_history_parts,
                    str(task.get("run_id") or ""),
                )
            final_gate = _assert_ttab_accepted(raw_root, expected_history_parts)
            result["final_ttab_gate"] = _ttab_gate_result(final_gate)
            gate = final_gate
        else:
            raise ValueError(f"Unsupported US TTAB action: {action}")
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
    except DomainTaskInterrupted as exc:
        finish_job_run(run_id, "INTERRUPTED", error_message=str(exc))
    except DomainTaskBlocked as exc:
        finish_job_run(run_id, "BLOCKED", error_message=str(exc))
    except Exception as exc:
        finish_job_run(run_id, "FAILED", error_message=f"{type(exc).__name__}: {exc}")
    else:
        finish_job_run(run_id, "SUCCESS", metrics=metrics)
