from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from app.db import postgres_conn


OPERATIONS_VERSION = "MARKORBIT_OPERATIONS_V2"
OPERATION_STATES = (
    "READY",
    "QUEUED",
    "RUNNING",
    "STOPPING",
    "PAUSED",
    "RESUME_CANDIDATE",
    "RETRY_CANDIDATE",
    "BLOCKED",
    "NEEDS_OPERATOR",
    "COMPLETE",
    "IDLE",
)
ACTION_AUTHORITY = (
    "ADVISORY_ONLY_EXISTING_DOMAIN_GATES_AND_CHECKPOINT_VALIDATORS_REMAIN_AUTHORITATIVE"
)

_DOMAIN_BY_JURISDICTION = {
    "CN": "CN",
    "US": "US_APPLICATION",
    "US_ASSIGNMENT": "US_ASSIGNMENT",
    "US_TTAB": "US_TTAB",
}


@dataclass(frozen=True)
class PackageOperationEvidence:
    package_id: str
    domain: str
    package_status: str
    package_kind: str = ""
    file_name: str = ""
    error_message: str = ""
    latest_job_status: str = ""
    latest_job_run_id: str = ""
    latest_job_error: str = ""
    stage_checkpoint_version: str = ""
    publish_checkpoint_version: str = ""
    publish_success_tasks: int = 0
    publish_failed_tasks: int = 0
    publish_running_tasks: int = 0
    publish_task_total: int = 0
    publish_resume_task_index: int | None = None

    @property
    def has_stage_checkpoint(self) -> bool:
        return bool(self.stage_checkpoint_version)

    @property
    def has_publish_checkpoint(self) -> bool:
        return bool(self.publish_checkpoint_version)


@dataclass(frozen=True)
class DomainTaskEvidence:
    run_id: str
    domain: str
    action: str
    status: str
    stop_requested: bool = False
    error_message: str = ""


def _operation_record(
    *,
    owner_kind: str,
    owner_id: str,
    domain: str,
    state: str,
    next_safe_action: str,
    reason_codes: list[str],
    preserve_partial_state: bool,
    verification_required: bool,
    operator_required: bool,
    progress: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in OPERATION_STATES:
        raise ValueError(f"unsupported operation state: {state}")
    return {
        "operations_version": OPERATIONS_VERSION,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "domain": domain,
        "state": state,
        "next_safe_action": next_safe_action,
        "reason_codes": list(reason_codes),
        "preserve_partial_state": bool(preserve_partial_state),
        "verification_required": bool(verification_required),
        "operator_required": bool(operator_required),
        "action_authority": ACTION_AUTHORITY,
        "progress": dict(progress or {}),
        "details": dict(details or {}),
    }


def classify_package_operation(evidence: PackageOperationEvidence) -> dict[str, Any]:
    """Classify package recovery without authorizing a mutation.

    Checkpoint presence is only a resume *candidate*. The existing domain-specific
    validators still have to prove that temporary ClickHouse state exactly matches
    the durable checkpoint before execution may continue.
    """

    status = evidence.package_status.strip().upper()
    job_status = evidence.latest_job_status.strip().upper()
    details = {
        "package_status": status,
        "package_kind": evidence.package_kind,
        "file_name": evidence.file_name,
        "latest_job_status": job_status,
        "latest_job_run_id": evidence.latest_job_run_id,
        "package_error": evidence.error_message,
        "latest_job_error": evidence.latest_job_error,
        "stage_checkpoint_version": evidence.stage_checkpoint_version,
        "publish_checkpoint_version": evidence.publish_checkpoint_version,
    }

    publish_progress: dict[str, Any] = {}
    if evidence.has_publish_checkpoint:
        denominator = max(
            int(evidence.publish_task_total or 0),
            int(evidence.publish_success_tasks)
            + int(evidence.publish_failed_tasks)
            + int(evidence.publish_running_tasks),
        )
        completed = int(evidence.publish_success_tasks)
        publish_progress = {
            "work_total": denominator,
            "work_success": completed,
            "work_failed": int(evidence.publish_failed_tasks),
            "work_running": int(evidence.publish_running_tasks),
            "resume_task_index": evidence.publish_resume_task_index,
            "progress_pct": round(completed / denominator * 100, 2) if denominator else 0.0,
        }

    if status == "SUCCESS":
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="COMPLETE",
            next_safe_action="NONE",
            reason_codes=["PACKAGE_ACCEPTED_SUCCESS"],
            preserve_partial_state=False,
            verification_required=False,
            operator_required=False,
            progress=publish_progress,
            details=details,
        )

    if job_status == "RUNNING":
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="RUNNING",
            next_safe_action="WAIT_FOR_CURRENT_UNIT_BOUNDARY",
            reason_codes=["ACTIVE_JOB_RUNNING"],
            preserve_partial_state=evidence.has_stage_checkpoint
            or evidence.has_publish_checkpoint,
            verification_required=False,
            operator_required=False,
            progress=publish_progress,
            details=details,
        )

    if job_status == "QUEUED":
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="QUEUED",
            next_safe_action="WAIT_FOR_WORKER_CLAIM",
            reason_codes=["JOB_ALREADY_QUEUED"],
            preserve_partial_state=evidence.has_stage_checkpoint
            or evidence.has_publish_checkpoint,
            verification_required=False,
            operator_required=False,
            progress=publish_progress,
            details=details,
        )

    if status == "MISSING_FILE":
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="BLOCKED",
            next_safe_action="RESTORE_OR_LOCATE_REGISTERED_SOURCE",
            reason_codes=["REGISTERED_SOURCE_FILE_MISSING"],
            preserve_partial_state=evidence.has_stage_checkpoint
            or evidence.has_publish_checkpoint,
            verification_required=True,
            operator_required=True,
            progress=publish_progress,
            details=details,
        )

    if evidence.has_publish_checkpoint:
        reasons = ["FINAL_PUBLISH_CHECKPOINT_PRESENT"]
        if evidence.publish_failed_tasks:
            reasons.append("FAILED_WORK_UNITS_RECORDED")
        if evidence.publish_running_tasks:
            reasons.append("INTERRUPTED_RUNNING_WORK_UNITS_RECORDED")
        if evidence.publish_success_tasks:
            reasons.append("COMPLETED_WORK_UNITS_MUST_NOT_BE_REPLAYED")
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="RESUME_CANDIDATE",
            next_safe_action="VERIFY_FINAL_CHECKPOINT_THEN_CONTINUE_WORK_UNIT",
            reason_codes=reasons,
            preserve_partial_state=True,
            verification_required=True,
            operator_required=False,
            progress=publish_progress,
            details=details,
        )

    if evidence.has_stage_checkpoint:
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="RESUME_CANDIDATE",
            next_safe_action="VERIFY_STAGE_CHECKPOINT_THEN_RESUME_POST_STAGE",
            reason_codes=["POST_STAGE_CHECKPOINT_PRESENT"],
            preserve_partial_state=True,
            verification_required=True,
            operator_required=False,
            progress=publish_progress,
            details=details,
        )

    if status == "PROCESSING":
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="NEEDS_OPERATOR",
            next_safe_action="INSPECT_ORPHAN_PROCESSING_STATE",
            reason_codes=["PROCESSING_WITHOUT_ACTIVE_JOB_OR_RECOVERY_CHECKPOINT"],
            preserve_partial_state=True,
            verification_required=True,
            operator_required=True,
            progress=publish_progress,
            details=details,
        )

    if status in {"FAILED", "INTERRUPTED"}:
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="RETRY_CANDIDATE",
            next_safe_action="VERIFY_SOURCE_AND_DOMAIN_GATES_THEN_RETRY_PACKAGE",
            reason_codes=[f"PACKAGE_{status}", "NO_DURABLE_RESUME_CHECKPOINT"],
            preserve_partial_state=False,
            verification_required=True,
            operator_required=False,
            progress=publish_progress,
            details=details,
        )

    if status == "REGISTERED":
        return _operation_record(
            owner_kind="PACKAGE",
            owner_id=evidence.package_id,
            domain=evidence.domain,
            state="READY",
            next_safe_action="START_THROUGH_EXISTING_DOMAIN_GATE",
            reason_codes=["REGISTERED_NOT_YET_PROCESSED"],
            preserve_partial_state=False,
            verification_required=True,
            operator_required=False,
            progress=publish_progress,
            details=details,
        )

    return _operation_record(
        owner_kind="PACKAGE",
        owner_id=evidence.package_id,
        domain=evidence.domain,
        state="IDLE",
        next_safe_action="NONE",
        reason_codes=["NO_ACTIONABLE_OPERATION_STATE"],
        preserve_partial_state=evidence.has_stage_checkpoint
        or evidence.has_publish_checkpoint,
        verification_required=False,
        operator_required=False,
        progress=publish_progress,
        details=details,
    )


def classify_domain_task_operation(evidence: DomainTaskEvidence) -> dict[str, Any]:
    status = evidence.status.strip().upper()
    details = {
        "requested_action": evidence.action.strip().upper(),
        "task_status": status,
        "stop_requested": bool(evidence.stop_requested),
        "error_message": evidence.error_message,
    }
    if status == "QUEUED":
        state, action, reasons = "QUEUED", "WAIT_FOR_WORKER_CLAIM", ["DOMAIN_TASK_QUEUED"]
    elif status == "RUNNING" and evidence.stop_requested:
        state, action, reasons = (
            "STOPPING",
            "WAIT_FOR_SAFE_PACKAGE_BOUNDARY",
            ["COOPERATIVE_STOP_REQUESTED"],
        )
    elif status == "RUNNING":
        state, action, reasons = "RUNNING", "WAIT_FOR_CURRENT_UNIT_BOUNDARY", ["DOMAIN_TASK_RUNNING"]
    elif status == "INTERRUPTED" and evidence.stop_requested:
        state, action, reasons = (
            "PAUSED",
            "CONTINUE_THROUGH_EXISTING_DOMAIN_GATE",
            ["COOPERATIVE_STOP_COMPLETED"],
        )
    elif status == "FAILED":
        state, action, reasons = (
            "RETRY_CANDIDATE",
            "RECHECK_DOMAIN_GATE_THEN_RETRY_DOMAIN_TASK",
            ["DOMAIN_TASK_FAILED"],
        )
    elif status == "SUCCESS":
        state, action, reasons = "COMPLETE", "NONE", ["DOMAIN_TASK_SUCCESS"]
    else:
        state, action, reasons = "IDLE", "NONE", ["NO_ACTIONABLE_DOMAIN_TASK_STATE"]
    return _operation_record(
        owner_kind="DOMAIN_TASK",
        owner_id=evidence.run_id,
        domain=evidence.domain,
        state=state,
        next_safe_action=action,
        reason_codes=reasons,
        preserve_partial_state=state in {"RUNNING", "STOPPING", "PAUSED", "RETRY_CANDIDATE"},
        verification_required=state in {"PAUSED", "RETRY_CANDIDATE"},
        operator_required=False,
        details=details,
    )


def _package_evidence(limit: int) -> list[PackageOperationEvidence]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    sp.package_id::text AS package_id,
                    sp.jurisdiction,
                    sp.status AS package_status,
                    sp.package_kind,
                    sp.file_name,
                    coalesce(sp.error_message, '') AS package_error,
                    coalesce(stage.checkpoint_version, '') AS stage_checkpoint_version,
                    coalesce(pub.checkpoint_version, '') AS publish_checkpoint_version,
                    coalesce(tasks.success_tasks, 0) AS success_tasks,
                    coalesce(tasks.failed_tasks, 0) AS failed_tasks,
                    coalesce(tasks.running_tasks, 0) AS running_tasks,
                    coalesce(tasks.task_total, 0) AS task_total,
                    tasks.resume_task_index,
                    coalesce(job.run_id::text, '') AS latest_job_run_id,
                    coalesce(job.status, '') AS latest_job_status,
                    coalesce(job.error_message, '') AS latest_job_error
                FROM control.source_package AS sp
                LEFT JOIN control.cn_package_stage_checkpoint AS stage
                  ON stage.package_id = sp.package_id
                LEFT JOIN control.cn_publish_checkpoint AS pub
                  ON pub.package_id = sp.package_id
                LEFT JOIN LATERAL (
                    SELECT
                        count(*) FILTER (WHERE status = 'SUCCESS') AS success_tasks,
                        count(*) FILTER (WHERE status = 'FAILED') AS failed_tasks,
                        count(*) FILTER (WHERE status = 'RUNNING') AS running_tasks,
                        max(task_total) AS task_total,
                        min(task_index) FILTER (WHERE status IN ('FAILED', 'RUNNING'))
                            AS resume_task_index
                    FROM control.cn_publish_subtask
                    WHERE package_id = sp.package_id
                ) AS tasks ON true
                LEFT JOIN LATERAL (
                    SELECT run_id, status, error_message
                    FROM control.job_run
                    WHERE payload->>'package_id' = sp.package_id::text
                    ORDER BY started_at DESC, run_id DESC
                    LIMIT 1
                ) AS job ON true
                WHERE sp.status != 'SUCCESS'
                   OR stage.package_id IS NOT NULL
                   OR pub.package_id IS NOT NULL
                ORDER BY sp.source_rank DESC, sp.package_sequence DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = list(cur.fetchall())
    result: list[PackageOperationEvidence] = []
    for row in rows:
        jurisdiction = str(row.get("jurisdiction") or "").upper()
        result.append(
            PackageOperationEvidence(
                package_id=str(row["package_id"]),
                domain=_DOMAIN_BY_JURISDICTION.get(jurisdiction, jurisdiction or "OTHER"),
                package_status=str(row.get("package_status") or ""),
                package_kind=str(row.get("package_kind") or ""),
                file_name=str(row.get("file_name") or ""),
                error_message=str(row.get("package_error") or ""),
                latest_job_status=str(row.get("latest_job_status") or ""),
                latest_job_run_id=str(row.get("latest_job_run_id") or ""),
                latest_job_error=str(row.get("latest_job_error") or ""),
                stage_checkpoint_version=str(row.get("stage_checkpoint_version") or ""),
                publish_checkpoint_version=str(row.get("publish_checkpoint_version") or ""),
                publish_success_tasks=int(row.get("success_tasks") or 0),
                publish_failed_tasks=int(row.get("failed_tasks") or 0),
                publish_running_tasks=int(row.get("running_tasks") or 0),
                publish_task_total=int(row.get("task_total") or 0),
                publish_resume_task_index=(
                    int(row["resume_task_index"])
                    if row.get("resume_task_index") is not None
                    else None
                ),
            )
        )
    return result


def _domain_task_evidence(limit: int) -> list[DomainTaskEvidence]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id::text, status, payload, coalesce(error_message, '') AS error_message
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = 'DOMAIN_CONTROL'
                ORDER BY started_at DESC, run_id DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = list(cur.fetchall())
    result: list[DomainTaskEvidence] = []
    for row in rows:
        payload = dict(row.get("payload") or {})
        result.append(
            DomainTaskEvidence(
                run_id=str(row["run_id"]),
                domain=str(payload.get("domain") or "OTHER").upper(),
                action=str(payload.get("action") or ""),
                status=str(row.get("status") or ""),
                stop_requested=bool(payload.get("stop_requested") or False),
                error_message=str(row.get("error_message") or ""),
            )
        )
    return result


def operations_snapshot(*, package_limit: int = 200, task_limit: int = 50) -> dict[str, Any]:
    packages = [classify_package_operation(item) for item in _package_evidence(package_limit)]
    domain_tasks = [
        classify_domain_task_operation(item) for item in _domain_task_evidence(task_limit)
    ]
    operations = domain_tasks + packages
    counts = Counter(item["state"] for item in operations)
    return {
        "version": OPERATIONS_VERSION,
        "action_authority": ACTION_AUTHORITY,
        "summary": {
            "operation_count": len(operations),
            "state_counts": dict(sorted(counts.items())),
            "resume_candidates": counts.get("RESUME_CANDIDATE", 0),
            "retry_candidates": counts.get("RETRY_CANDIDATE", 0),
            "operator_required": sum(
                1 for item in operations if item.get("operator_required")
            ),
            "partial_state_preservation_required": sum(
                1 for item in operations if item.get("preserve_partial_state")
            ),
        },
        "operations": operations,
    }


def operations_contract() -> dict[str, Any]:
    return {
        "version": OPERATIONS_VERSION,
        "role": "READ_ONLY_RECOVERY_AND_OPERATOR_DECISION_MODEL",
        "states": list(OPERATION_STATES),
        "action_authority": ACTION_AUTHORITY,
        "invariants": {
            "checkpoint_presence_is_candidate_not_proof": True,
            "checkpoint_validation_required_before_resume": True,
            "completed_work_units_must_not_be_replayed": True,
            "resumable_failure_preserves_checkpoint_and_temporary_outputs": True,
            "cleanup_of_resumable_state_only_after_package_success": True,
            "processing_without_job_or_checkpoint_fails_to_operator_review": True,
            "registered_source_missing_blocks_retry": True,
            "existing_domain_transition_gates_remain_authoritative": True,
            "operations_view_does_not_mutate_data": True,
            "operations_view_does_not_authorize_legal_or_business_action": True,
        },
        "live_evidence": [
            "control.source_package",
            "control.job_run",
            "control.cn_package_stage_checkpoint",
            "control.cn_publish_checkpoint",
            "control.cn_publish_subtask",
        ],
        "current_live_adapters": ["CN_PACKAGE_RECOVERY", "ADMIN_DOMAIN_TASKS"],
        "future_adapter_rule": (
            "NEW_DOMAINS_ADD_OPERATION_EVIDENCE_ADAPTERS_WITHOUT_CHANGING_STATE_SEMANTICS"
        ),
    }
