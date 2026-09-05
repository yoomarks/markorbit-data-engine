from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.admin_domain_tasks import queue_admin_domain_task, request_admin_domain_stop
from app.us.target_bulk_task_control import (
    resumable_target_bulk_task,
    resume_target_bulk_task,
)
from app.us.target_bulk_tasks import (
    active_target_bulk_task,
    approve_target_bulk_task,
    queue_target_bulk_prepare,
    request_target_bulk_stop,
)


router = APIRouter(prefix="/api/admin/v2/domain-tasks", tags=["admin-domain-tasks"])


def _is_us_application(domain: str) -> bool:
    return domain.strip().upper() == "US_APPLICATION"


def _queue_us_application_target_task(
    *,
    action: str,
    expected_history_parts: int,
    bulk_end_sequence: int | None,
    bulk_max_packages: int | None,
):
    action = action.strip().upper()
    if expected_history_parts not in {0, 91}:
        raise ValueError(
            "US Application target bulk corpus is frozen at expected_history_parts=91"
        )
    if action in {"RUN", "CONTINUE"}:
        resumable = resumable_target_bulk_task()
        if resumable is not None:
            return {
                "accepted": False,
                "task": resumable,
                "resume_required": True,
                "message": (
                    "A frozen US Application target bulk task already has durable state; "
                    "use RETRY/RESUME instead of creating a new plan."
                ),
            }
    if action == "RUN":
        if bulk_end_sequence is not None or bulk_max_packages is not None:
            raise ValueError("US Application RUN is fixed to exactly one suffix package")
        return queue_target_bulk_prepare(max_packages=1)
    if action == "RETRY":
        if bulk_end_sequence is not None or bulk_max_packages is not None:
            raise ValueError("US Application RETRY resumes its existing frozen plan")
        return resume_target_bulk_task()
    if action == "CONTINUE":
        if bulk_end_sequence is not None and bulk_max_packages is not None:
            raise ValueError("provide only one of bulk_end_sequence or bulk_max_packages")
        if bulk_end_sequence is None and bulk_max_packages is None:
            bulk_end_sequence = 310
        return queue_target_bulk_prepare(
            end_sequence=bulk_end_sequence,
            max_packages=bulk_max_packages,
        )
    raise ValueError(f"Unsupported US Application action: {action}")


@router.post("/{domain}/{action}", status_code=202)
def admin_domain_task(
    domain: str,
    action: str,
    expected_history_parts: int = Query(default=0, ge=0, le=9999),
    bulk_end_sequence: int | None = Query(default=None, ge=3, le=310),
    bulk_max_packages: int | None = Query(default=None, ge=1, le=308),
):
    try:
        normalized_action = action.strip().upper()
        # Keep the direct normalization expression here as part of the established
        # Admin STOP source contract while reusing normalized_action below.
        if action.strip().upper() == "STOP":
            if _is_us_application(domain) and active_target_bulk_task() is not None:
                return request_target_bulk_stop()
            return request_admin_domain_stop(domain=domain)
        if _is_us_application(domain):
            return _queue_us_application_target_task(
                action=normalized_action,
                expected_history_parts=expected_history_parts,
                bulk_end_sequence=bulk_end_sequence,
                bulk_max_packages=bulk_max_packages,
            )
        return queue_admin_domain_task(
            domain=domain,
            action=action,
            expected_history_parts=expected_history_parts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/US_APPLICATION/BULK/ACTIVE")
def us_application_target_bulk_active():
    return {
        "task": active_target_bulk_task(),
        "resumable_task": resumable_target_bulk_task(),
    }


@router.post("/US_APPLICATION/BULK/{run_id}/APPROVE", status_code=202)
def us_application_target_bulk_approve(
    run_id: str,
    plan_sha256: str = Query(min_length=64, max_length=64),
):
    try:
        return approve_target_bulk_task(run_id=run_id, plan_sha256=plan_sha256)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/US_APPLICATION/BULK/{run_id}/RESUME", status_code=202)
def us_application_target_bulk_resume(run_id: str):
    try:
        return resume_target_bulk_task(run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
