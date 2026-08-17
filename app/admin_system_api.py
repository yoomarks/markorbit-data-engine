from __future__ import annotations

from fastapi import APIRouter, Query

from app.admin_progress import domain_progress_snapshot
from app.component_versions import component_versions
from app.operations_v2 import operations_snapshot
from app.version import engine_version


router = APIRouter(prefix="/api/admin/v2/system", tags=["admin-system"])


@router.get("/components")
def admin_component_versions():
    """Expose the existing component-version matrix to the local admin UI."""
    return {
        "engine_version": engine_version(),
        **component_versions(),
    }


@router.get("/operations")
def admin_operations_snapshot(
    package_limit: int = Query(default=200, ge=1, le=1000),
    task_limit: int = Query(default=50, ge=1, le=500),
):
    """Read-only recovery view; existing domain gates remain mutation authority."""
    return operations_snapshot(package_limit=package_limit, task_limit=task_limit)


@router.get("/domain-progress")
def admin_domain_progress():
    """Expose durable, read-only progress for long-running Admin domain tasks."""
    return domain_progress_snapshot()
