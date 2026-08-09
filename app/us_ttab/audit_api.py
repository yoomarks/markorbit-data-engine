from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.us_ttab.audit_real_data import build_audit
from app.us_ttab.readiness import build_readiness


router = APIRouter(prefix="/api/us/ttab", tags=["US TTAB acceptance"])


@router.get("/acceptance")
def us_ttab_acceptance(verify_sources: bool = False):
    try:
        return build_audit(raw_root=Path(get_settings().raw_data_root), verify_sources=verify_sources)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_TTAB_ACCEPTANCE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc


@router.get("/readiness")
def us_ttab_readiness(verify_sources: bool = False):
    try:
        return build_readiness(raw_root=Path(get_settings().raw_data_root), verify_sources=verify_sources)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_TTAB_READINESS_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
