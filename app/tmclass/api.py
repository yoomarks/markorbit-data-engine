from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.fact_admission_security import require_fact_admission_auth
from app.tmclass.contract import TmclassAdmissionError
from app.tmclass.repository import admit_tmclass_evidence


router = APIRouter(
    prefix="/api/admin/v2/fact-admissions/tmclass",
    tags=["tmclass-fact-admissions"],
    dependencies=[Depends(require_fact_admission_auth)],
)


@router.post("/evidence")
def admit_evidence(package: dict[str, Any]) -> dict[str, Any]:
    try:
        return admit_tmclass_evidence(package)
    except TmclassAdmissionError as exc:
        raise HTTPException(
            400,
            detail={
                "code": "TMCLASS_EVIDENCE_REJECTED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "code": "TMCLASS_FACT_STORE_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
