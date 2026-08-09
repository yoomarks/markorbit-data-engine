from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.us_assignment.audit_real_data import build_audit
from app.us_assignment.readiness import build_readiness
from app.us_assignment.reconciliation import scan_reconciliation_page


router = APIRouter(prefix="/api/us/assignments", tags=["US assignment acceptance"])


@router.get("/acceptance")
def us_assignment_acceptance(verify_sources: bool = False):
    try:
        return build_audit(
            raw_root=Path(get_settings().raw_data_root),
            verify_sources=verify_sources,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_ASSIGNMENT_ACCEPTANCE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc


@router.get("/readiness")
def us_assignment_readiness(verify_sources: bool = False):
    try:
        return build_readiness(
            raw_root=Path(get_settings().raw_data_root),
            verify_sources=verify_sources,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_ASSIGNMENT_READINESS_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc


@router.get("/reconciliation")
def us_assignment_reconciliation_page(
    after_serial: str = "",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    try:
        return scan_reconciliation_page(after_serial=after_serial, limit=limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_ASSIGNMENT_RECONCILIATION_INPUT_INVALID", "error": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_ASSIGNMENT_RECONCILIATION_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
