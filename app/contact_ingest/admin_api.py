from __future__ import annotations

import logging
from pathlib import Path
import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.contact_ingest.directory_api import (
    contact_directory_analytics,
    contact_directory_list,
)
from app.contact_ingest.task_queue import (
    apply_contact_task,
    contact_task_summary,
    get_contact_task,
    list_contact_tasks,
    scan_contact_incoming,
    start_contact_task_scanner,
)


router = APIRouter(tags=["contact-admin"])
_logger = logging.getLogger("markorbit.contact-admin")


def _apply_in_background(task_id: str) -> None:
    try:
        apply_contact_task(task_id)
    except Exception:
        _logger.exception("Background contact import failed: task_id=%s", task_id)


def _apply_batch_in_background(task_ids: list[str]) -> None:
    for task_id in task_ids:
        _apply_in_background(task_id)


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


@router.get("/api/admin/contacts/directory/analytics")
def admin_contact_directory_analytics():
    return contact_directory_analytics()


@router.get("/api/admin/contacts/directory")
def admin_contact_directory(
    country: str = "",
    segment: str = "",
    channel: str = "",
    q: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return contact_directory_list(
        country=country,
        segment=segment,
        channel=channel,
        query=q,
        limit=limit,
        offset=offset,
    )


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
    # The Control Center expects this endpoint to return the real scan metrics.
    # Automatic periodic discovery already runs in the background; the explicit
    # operator button intentionally waits for one deterministic scan result.
    return scan_contact_incoming()


@router.post("/api/admin/contacts/tasks/batch-apply", status_code=202)
def admin_contact_batch_apply():
    """Queue every currently READY contact task as one sequential background batch."""
    ready_tasks = list_contact_tasks(status="READY", limit=1000)
    task_ids = [str(task["task_id"]) for task in reversed(ready_tasks)]
    if not task_ids:
        return {
            "status": "IDLE",
            "accepted_count": 0,
            "task_ids": [],
            "background": False,
        }

    thread = threading.Thread(
        target=_apply_batch_in_background,
        args=(task_ids,),
        name="contact-import-batch",
        daemon=True,
    )
    thread.start()
    return {
        "status": "PROCESSING",
        "accepted_count": len(task_ids),
        "task_ids": task_ids,
        "background": True,
    }


@router.post("/api/admin/contacts/tasks/{task_id}/apply", status_code=202)
def admin_contact_apply(task_id: str):
    """Queue one explicit contact import without holding the browser request open."""
    try:
        task = get_contact_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    status = str(task.get("status") or "")
    if status == "SUCCESS":
        return {"status": "SUCCESS", "already_completed": True, "task_id": task_id}
    if status == "PROCESSING":
        return {"status": "PROCESSING", "already_running": True, "task_id": task_id}
    if status not in {"READY", "FAILED"}:
        raise HTTPException(
            status_code=409,
            detail=f"Contact task is not executable from status {status}",
        )

    thread = threading.Thread(
        target=_apply_in_background,
        args=(task_id,),
        name=f"contact-import-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"status": "PROCESSING", "task_id": task_id, "background": True}
