from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.admin_domain_tasks import queue_admin_domain_task, request_admin_domain_stop


router = APIRouter(prefix="/api/admin/v2/domain-tasks", tags=["admin-domain-tasks"])


@router.post("/{domain}/{action}", status_code=202)
def admin_domain_task(
    domain: str,
    action: str,
    expected_history_parts: int = Query(default=0, ge=0, le=9999),
):
    try:
        if action.strip().upper() == "STOP":
            return request_admin_domain_stop(domain=domain)
        return queue_admin_domain_task(
            domain=domain,
            action=action,
            expected_history_parts=expected_history_parts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
