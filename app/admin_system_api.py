from __future__ import annotations

from fastapi import APIRouter

from app.component_versions import component_versions
from app.version import engine_version


router = APIRouter(prefix="/api/admin/v2/system", tags=["admin-system"])


@router.get("/components")
def admin_component_versions():
    """Expose the existing component-version matrix to the local admin UI."""
    return {
        "engine_version": engine_version(),
        **component_versions(),
    }
