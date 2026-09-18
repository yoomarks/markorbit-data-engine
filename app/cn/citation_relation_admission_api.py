from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.cn.citation_relation_admission import (
    CitationRelationAdmissionError,
    admit_cn_citation_relation,
)
from app.db import clickhouse_client
from app.fact_admission_security import require_fact_admission_auth


router = APIRouter(
    prefix="/api/admin/v2/fact-admissions",
    tags=["fact-admissions"],
    dependencies=[Depends(require_fact_admission_auth)],
)


@router.post("/cn/citation-relations")
def admit_cn_citation(candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        return admit_cn_citation_relation(
            candidate,
            client=clickhouse_client(),
        ).as_dict()
    except CitationRelationAdmissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_FACT_ADMISSION_REJECTED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_FACT_ADMISSION_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
