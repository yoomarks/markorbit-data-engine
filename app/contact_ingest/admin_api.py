from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.contact_ingest.task_queue import (
    apply_contact_task,
    contact_task_summary,
    get_contact_task,
    list_contact_tasks,
    scan_contact_incoming,
    start_contact_task_scanner,
)


router = APIRouter(tags=["contact-admin"])


@router.on_event("startup")
def start_contact_discovery() -> None:
    start_contact_task_scanner()


@router.get("/contacts", include_in_schema=False)
def contact_control_center():
    for candidate in (Path("/app/web/contacts.html"), Path("web/contacts.html")):
        if candidate.exists():
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="Contact Control Center not found")


@router.get("/api/admin/contacts/summary")
def admin_contact_summary():
    return contact_task_summary()


@router.get("/api/admin/contacts/tasks")
def admin_contact_tasks(
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    return list_contact_tasks(status=status, limit=limit)


@router.get("/api/admin/contacts/tasks/{task_id}")
def admin_contact_task_detail(task_id: str):
    try:
        return get_contact_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/admin/contacts/scan")
def admin_contact_scan():
    return scan_contact_incoming()


@router.post("/api/admin/contacts/tasks/{task_id}/apply")
def admin_contact_apply(task_id: str):
    try:
        return apply_contact_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error_type": type(exc).__name__, "error": str(exc)},
        ) from exc
